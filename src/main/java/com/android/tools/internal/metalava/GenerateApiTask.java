/*
 * Copyright (C) 2022 The Android Open Source Project
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

package com.android.tools.internal.metalava;

import com.android.tools.internal.AgpVersion;
import com.android.tools.internal.TaskUtils;
import com.google.common.collect.Lists;
import org.gradle.api.DefaultTask;
import org.gradle.api.file.*;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.*;
import org.gradle.workers.WorkerExecutor;
import javax.inject.Inject;
import java.io.File;
import java.util.*;
import java.util.stream.Collectors;

import static com.android.tools.internal.metalava.MetalavaPlugin.API_LEVELS_FILE;

/**
 * Task to generate the current API from the source files
 * <p>
 * Adapted from <a href="https://cs.android.com/androidx/platform/frameworks/support/+/androidx-main:buildSrc/private/src/main/kotlin/androidx/build/metalava/">androidx</a>
 */
public abstract class GenerateApiTask extends DefaultTask {

    @Internal
    public abstract DirectoryProperty getJdkHome();

    @Internal
    public abstract Property<String> getAgpVersion();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract ConfigurableFileCollection getSourcePaths();

    @CompileClasspath
    public abstract ConfigurableFileCollection getClasspath();

    @Classpath
    public abstract ConfigurableFileCollection getMetalavaClasspath();

    @OutputDirectory
    public abstract DirectoryProperty getOutputDirectory();

    @Inject
    public abstract WorkerExecutor getWorkerExecutor();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract ConfigurableFileCollection getOldApiFiles();

    @TaskAction
    public void generateApi() {
        List<String> args = Lists.newArrayList(
                "--error",
                "UnresolvedImport",
                "--delete-empty-removed-signatures",
                "--source-path",
                TaskUtils.asArg(getSourcePaths()),
                "--format=v4",
                "--warnings-as-errors",
                "--jdk-home",
                TaskUtils.asArg(getJdkHome()),
                "--classpath",
                TaskUtils.asArg(getClasspath()),
                "--api",
                TaskUtils.asArg(getOutputDirectory()) + "/current.txt",
                "--removed-api",
                TaskUtils.asArg(getOutputDirectory()) + "/removed_current.txt"
        );

        args.addAll(getGenerateApiLevelsArgs(getFilesForApiLevels(getOldApiFiles().getFiles()), getAgpVersion()));

        getWorkerExecutor().noIsolation().submit(
                MetalavaWorkAction.class,
                params -> {
                    params.getArgs().set(args);
                    params.getMetalavaClasspath().set(getMetalavaClasspath());
                    params.getK2UastEnabled().set(true);
                }
        );
    }

    private List<String> getGenerateApiLevelsArgs(SortedMap<AgpVersion, File> apiFiles, Property<String> currentVersion) {
        if (apiFiles.isEmpty()) return Collections.emptyList();

        List<AgpVersion> versions = Lists.newArrayList(new TreeSet<>(apiFiles.keySet()));
        versions.add(AgpVersion.parseOrNull(currentVersion.get()));
        String apiVersionNames = versions.stream().map(AgpVersion::toString).collect(Collectors.joining(" "));
        List<String> args =
            Lists.newArrayList(
                "--generate-api-version-history",
                TaskUtils.asArg(getOutputDirectory()) + "/" + API_LEVELS_FILE,
                "--api-version-names",
                apiVersionNames
            );

        String apiFilesString = apiFiles.values().stream().map(File::toString).collect(Collectors.joining(File.pathSeparator));
        args.addAll(List.of("--api-version-signature-files", apiFilesString));

        return args;
    }

    private SortedMap<AgpVersion, File> getFilesForApiLevels(Set<File> files) {
        SortedMap<AgpVersion, File> versionFileMap = new TreeMap<>();
        // create a map from AGP version to the file containing the API surface for that version
        for (File file: files) {
            if (file.getName().endsWith(".txt") && !file.getName().startsWith("current")) {
                AgpVersion version = AgpVersion.parseFileNameOrNull(file.getName());
                if (version != null) {
                    versionFileMap.put(version, file);
                }
            }
        }

        return versionFileMap;
    }
}