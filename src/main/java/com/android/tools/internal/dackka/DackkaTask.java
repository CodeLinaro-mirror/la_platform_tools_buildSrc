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

import com.android.tools.internal.GsonUtils;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import javax.inject.Inject;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.collect.Lists;
import com.google.gson.Gson;
import org.gradle.api.DefaultTask;
import org.gradle.api.file.ConfigurableFileCollection;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.file.FileCollection;
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
 * <a href="https://cs.android.com/androidx/platform/frameworks/support/+/androidx-main:buildSrc/private/src/main/kotlin/androidx/build/dackka/DackkaTask.kt">androidx</a>
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
    public abstract ConfigurableFileCollection getSources();

    // Intermediate directory to extract sources to, doesn't need to be tracked as an input or output
    @Internal
    public abstract DirectoryProperty getExtractedSources();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract DirectoryProperty getPackageListsDirectory();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract ConfigurableFileCollection getVersionMetadataFiles();

    @OutputDirectory
    public abstract DirectoryProperty getDestinationDirectory();

    // List of annotations which should not be displayed in the docs
    private static final List<String> hiddenAnnotations = ImmutableList.of(
            "kotlin.OptIn",
            // This annotation is generated upstream. Dokka uses it for signature serialization.
            "kotlin.ParameterName",
            // This annotations is not useful for developers but right now is @ShowAnnotation?
            "kotlin.js.JsName",
            // This annotation is intended to target the compiler and is general not useful for devs.
            "java.lang.Override"
    );

    // Annotations which should not be displayed in the Kotlin docs, in addition to hiddenAnnotations
    private static final List<String> hiddenAnnotationsKotlin = ImmutableList.of("kotlin.ExtensionFunctionType");

    // Annotations which should not be displayed in the Java docs, in addition to hiddenAnnotations
    private static final List<String> hiddenAnnotationsJava = ImmutableList.of();

    private static final Map<String, String> externalLinks = ImmutableMap.<String, String>builder()
            .put("asm", "https://asm.ow2.io/javadoc/")
            .put("gradle", "https://docs.gradle.org/current/javadoc/")
            .put("kotlin", "https://kotlinlang.org/api/latest/jvm/stdlib/")
            .put("jdk", "https://docs.oracle.com/en/java/javase/11/docs/api/java.base/")
            .put("guava", "https://guava.dev/releases/18.0/api/docs/")
            .put("coroutinesCore", "https://kotlinlang.org/api/kotlinx.coroutines/")
            .put("junit", "https://junit.org/junit4/javadoc/4.12/")
            .put("okio", "https://square.github.io/okio/3.x/okio/")
            .put("protobuf", "https://protobuf.dev/reference/java/api-docs/")
            .put("grpc", "https://grpc.github.io/grpc-java/javadoc/")
            .put("mlkit", "https://developers.google.com/android/reference/")
            .put("dagger", "https://dagger.dev/api/latest/")
            .put("jetbrains-annotations", "https://javadoc.io/doc/org.jetbrains/annotations/latest/")
            .put("auto-value", "https://www.javadoc.io/doc/com.google.auto.value/auto-value/latest/")
            .build();

    /**
     * Documentation for Dackka command line usage and arguments can be found at
     * <a href="https://kotlin.github.io/dokka/1.4.0/user_guide/cli/usage/">dokka</a>
     * Documentation for the DevsitePlugin arguments can be found at
     * <a href="https://cs.android.com/androidx/platform/tools/dokka-devsite-plugin/+/master:src/main/java/com/google/devsite/DevsiteConfiguration.kt">devsite configuration</a>
     */
    private File computeArguments() throws IOException {
        String sourcesDir = getExtractedSources().get().getAsFile().getAbsolutePath();
        String outputDirectory = getDestinationDirectory().get().getAsFile().getAbsolutePath();
        Gson gson = GsonUtils.createGson();

        Map<String, Object> jsonMap = Map.of(
            "moduleName", "",
            "outputDir", outputDirectory,
            "globalLinks", "",
            "sourceSets", sourceSets(sourcesDir, getDependenciesClasspath()),
            "offlineMode", "true",
            "pluginsConfiguration",
            List.of(
                Map.of(
                    "fqPluginName", "com.google.devsite.DevsitePlugin",
                    "serializationFormat", "JSON",
                    "values",
                        gson.toJson(
                            Map.of(
                                "docRootPath", "reference/" + getDevsiteTenant().get(),
                                "kotlinDocsPath", "",
                                "projectPath", "",
                                "annotationsNotToDisplay", hiddenAnnotations,
                                "annotationsNotToDisplayJava", hiddenAnnotationsJava,
                                "annotationsNotToDisplayKotlin", hiddenAnnotationsKotlin,
                                "baseSourceLink", "https://cs.android.com/search?" + "q=file:%s+class:%s",
                                "versionMetadataFilenames", getVersionMetadataFiles().getFiles().stream().toList()
                            )
                        )
                )
            )
        );

        String json = gson.toJson(jsonMap);
        File outputFile = File.createTempFile("dackkaArgs", ".json");
        outputFile.deleteOnExit();
        FileWriter writer = new FileWriter(outputFile);
        writer.write(json);
        writer.flush();
        writer.close();
        return outputFile;
    }
    
    private List<DackkaInputModel.SourceSet> sourceSets(String sourcesDir, FileCollection dependenciesClasspath) {
        List<DackkaInputModel.SourceSetId> dependentSourceSets = List.of();
        List<DackkaInputModel.GlobalDocsLink> externalDocumentationLinks = Lists.newArrayList();

        for (Map.Entry<String, String> entry : externalLinks.entrySet()) {
            String name = entry.getKey();
            String url = entry.getValue();

            externalDocumentationLinks.add(new DackkaInputModel.GlobalDocsLink(
                url,
                "file://" + getPackageListsDirectory().get().getAsFile().toPath() + "/" + name + ""
            ));
        }

        List<DackkaInputModel.SrcLink> sourceLinks = List.of();
        List<String> documentedVisibilities = List.of("PUBLIC", "PROTECTED");

        return List.of(
            new DackkaInputModel.SourceSet(
                "main",
                new DackkaInputModel.SourceSetId("main", "android-gradle-plugin"),
                dependenciesClasspath,
                getProject().getObjects().fileCollection().from(sourcesDir),
                getProject().getObjects().fileCollection(), // samples
                getProject().getObjects().fileCollection(), // includes
                "jvm",
                documentedVisibilities,
                false,
                false,
                false,
                dependentSourceSets,
                externalDocumentationLinks,
                sourceLinks
            )
        );
    }

    @TaskAction
    public void generate() throws IOException {
        String argumentsFile = computeArguments().getPath();

        getWorkerExecutor()
                .noIsolation()
                .submit(
                        DackkaWorkAction.class,
                        parameters -> {
                            parameters.getDevsiteTenant().set(getDevsiteTenant());
                            parameters.getArgs().set(List.of(argumentsFile, "-loggingLevel", "WARN"));
                            parameters.getSources().from(getSources());
                            parameters.getExtractedSources().set(getExtractedSources());
                            parameters.getDestinationDirectory().set(getDestinationDirectory());
                            parameters.getDackkaClasspath().from(getDackkaClasspath());
                        });
    }

    interface DackkaParams extends WorkParameters {
        Property<String> getDevsiteTenant();

        ListProperty<String> getArgs();

        ConfigurableFileCollection getSources();

        DirectoryProperty getExtractedSources();
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
                clean(
                        getParameters().getDestinationDirectory().get().getAsFile().toPath());
                Path extractedSourcesDir = getParameters().getExtractedSources().get().getAsFile().toPath();
                Files.createDirectories(extractedSourcesDir);
                clean(extractedSourcesDir);
                if (getParameters().getSources().isEmpty()) {
                    throw new IOException("No sources specified, add projects to the api configuration");
                }
                for (File rootFile : getParameters().getSources()) {
                    Path root = rootFile.toPath();
                    if (!Files.isRegularFile(root)) {
                        throw new IOException("Expected " + root + " to be a file");
                    }
                    try(ZipInputStream zis = new ZipInputStream(new BufferedInputStream(Files.newInputStream(root)))) {
                        while(true) {
                            ZipEntry entry = zis.getNextEntry();
                            if (entry == null) break;
                            if (entry.isDirectory() || entry.getName().equals("NOTICE") || entry.getName().toUpperCase(Locale.US).startsWith("META-INF/") || entry.getName().contains("..")) continue;
                            Path file = extractedSourcesDir.resolve(entry.getName());
                            Files.createDirectories(file.getParent());
                            Files.copy(zis, file);
                        }
                    }

                }
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }

            getExecOperations()
                    .javaexec(
                            spec -> {
                                spec.getMainClass().set("org.jetbrains.dokka.MainKt");
                                spec.setArgs(getParameters().getArgs().get());
                                spec.setClasspath(getParameters().getDackkaClasspath());
                            });
        }

        static void clean(Path outDirectory) throws IOException {
            Files.createDirectories(outDirectory);
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