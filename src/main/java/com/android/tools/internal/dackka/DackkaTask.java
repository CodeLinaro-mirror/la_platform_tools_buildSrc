/*
 * Copyright (C) 2021 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.android.tools.internal.dackka;

import com.google.common.collect.ImmutableList;
import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.*;
import java.util.stream.Collectors;
import javax.inject.Inject;
import org.gradle.api.DefaultTask;
import org.gradle.api.file.ConfigurableFileCollection;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.provider.ListProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.*;
import org.gradle.process.ExecOperations;
import org.gradle.workers.WorkAction;
import org.gradle.workers.WorkParameters;
import org.gradle.workers.WorkerExecutor;

/**
 * Task to generate documentation for developer.android.com
 *
 * <p>Based on
 * https://cs.android.com/androidx/platform/frameworks/support/+/androidx-main:buildSrc/private/src/main/kotlin/androidx/build/dackka/DackkaTask.kt
 */
public abstract class DackkaTask extends DefaultTask {
    @Inject
    public abstract WorkerExecutor getWorkerExecutor();

    @Input
    public abstract Property<String> getDevsiteTenant();

    // Classpath containing Dackka
    @Classpath
    public abstract ConfigurableFileCollection getDackkaClasspath();

    // Classpath containing dependencies of libraries needed to resolve types in docs
    @Classpath
    public abstract ConfigurableFileCollection getDependenciesClasspath();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract DirectoryProperty getSourcesDir();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract DirectoryProperty getPackageListsDirectory();

    @OutputDirectory
    public abstract DirectoryProperty getDestinationDirectory();

    /**
     * Documentation for Dackka command line usage and arguments can be found at
     * https://kotlin.github.io/dokka/1.4.0/user_guide/cli/usage/
     */
    private List<String> computeArguments() throws IOException {
        // path comes with colons but dokka expects a semicolon delimited string
        String dependenciesClasspath =
                getDependenciesClasspath().getFiles().stream().map(File::getPath).collect(Collectors.joining(";"));
        Path indexFile =
                getPackageListsDirectory().get().getAsFile().toPath().resolve("index.properties");
        Properties index = new Properties();
        try (BufferedReader reader = Files.newBufferedReader(indexFile)) {
            index.load(reader);
        } catch (IOException e) {
            throw new IOException(
                    "Failed to load package list index file "
                            + indexFile
                            + "\nExpected format: relativeFilePath=url",
                    e);
        }
        Map<String, Path> urlToPackageList = new LinkedHashMap<>();
        for (Map.Entry<Object, Object> e : index.entrySet()) {
            Path entryFile = indexFile.resolveSibling(e.getKey().toString());
            if (!Files.isRegularFile(entryFile)) {
                throw new IOException("Index file entry not found: " + entryFile);
            }
            urlToPackageList.put(e.getValue().toString(), entryFile);
        }
        // //         Dokka sets this format: url^packageListUrl^^url2...
        String linksConfiguration =
                urlToPackageList.entrySet().stream()
                                .map(entry -> entry.getKey() + "^" + entry.getValue().toString())
                                .collect(Collectors.joining("^^"))
                        + "^^";

        String sourcesDir = getSourcesDir().get().getAsFile().getAbsolutePath();
        String outputDirectory = getDestinationDirectory().get().getAsFile().getAbsolutePath();

        return ImmutableList.of(

                // moduleName arg needs to be present but is not used the generated docs
                // b/184166302 tracks an update to the CLI to mark this as optional
                "-moduleName",
                "",

                // location of the generated docs
                "-outputDir",
                outputDirectory,

                // links to external types
                "-globalLinks",
                linksConfiguration,

                // Set logging level to only show warnings and errors
                "-loggingLevel",
                "WARN",

                // Configuration of sources. The generated string looks like this:
                // "-sourceSet -src /path/to/src -samples /path/to/samples ..."
                "-sourceSet",
                "-src " + sourcesDir + " -classpath " + dependenciesClasspath + " ",
                "-offlineMode");
    }

    @TaskAction
    public void generate() throws IOException {
        List<String> arguments = computeArguments();
        getWorkerExecutor()
                .noIsolation()
                .submit(
                        DackkaWorkAction.class,
                        parameters -> {
                            parameters.getDevsiteTenant().set(getDevsiteTenant());
                            parameters.getArgs().set(arguments);
                            parameters.getDestinationDirectory().set(getDestinationDirectory());
                            parameters.getDackkaClasspath().from(getDackkaClasspath());
                        });
    }

    interface DackkaParams extends WorkParameters {
        Property<String> getDevsiteTenant();

        ListProperty<String> getArgs();

        DirectoryProperty getDestinationDirectory();

        ConfigurableFileCollection getDackkaClasspath();
    }

    abstract static class DackkaWorkAction implements WorkAction<DackkaParams> {
        @Inject
        public abstract ExecOperations getExecOperations();

        @Inject
        public DackkaWorkAction() {}

        @Override
        public void execute() {
            try {
                clean();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }

            getExecOperations()
                    .javaexec(
                            spec -> {
                                spec.getMainClass().set("org.jetbrains.dokka.MainKt");
                                spec.setArgs(getParameters().getArgs().get());
                                spec.setClasspath(getParameters().getDackkaClasspath());
                                // b/183989795 tracks moving these away from an environment
                                // variables
                                spec.environment(
                                        "DEVSITE_TENANT_VERSIONED", getParameters().getDevsiteTenant().get()
                                );
                            });
        }

        private void clean() throws IOException {
            Path outDirectory =
                    getParameters().getDestinationDirectory().get().getAsFile().toPath();
            Files.walkFileTree(
                    outDirectory,
                    new SimpleFileVisitor<>() {
                        int depth = 0;

                        @Override
                        public FileVisitResult preVisitDirectory(
                                Path dir, BasicFileAttributes attrs) {
                            depth++;
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult visitFile(Path file, BasicFileAttributes attrs)
                                throws IOException {
                            Files.delete(file);
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult postVisitDirectory(Path dir, IOException exc)
                                throws IOException {
                            depth--;
                            if (depth > 0) {
                                Files.delete(dir);
                            }
                            return FileVisitResult.CONTINUE;
                        }
                    });
        }
    }
}
