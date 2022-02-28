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

import com.google.common.collect.ImmutableList;
import org.gradle.api.DefaultTask;
import org.gradle.api.file.*;
import org.gradle.api.tasks.*;
import org.gradle.workers.WorkerExecutor;

import javax.inject.Inject;
import java.io.File;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Task to generate the current API from the source files
 *
 * Adapted from https://cs.android.com/androidx/platform/frameworks/support/+/androidx-main:buildSrc/private/src/main/kotlin/androidx/build/metalava/
 */
public abstract class GenerateApiTask extends DefaultTask {

    @Internal
    public abstract DirectoryProperty getJdkHome();

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

    @TaskAction
    public void generateApi() {

        List<String> args = ImmutableList.of(
                "--no-banner",
                "--error",
                "UnresolvedImport",
                "--delete-empty-removed-signatures",
                "--source-path",
                asArg(getSourcePaths()),
                "--format=v4",
                "--output-kotlin-nulls=yes",
                "--warnings-as-errors",
                "--jdk-home",
                asArg(getJdkHome()),
                "--classpath",
                asArg(getClasspath()),
                "--api",
                asArg(getOutputDirectory()) + "/current.txt" // TODO: --removed-api ?
        );
        getWorkerExecutor().noIsolation().submit(
                MetalavaWorkAction.class,
                params -> {
                    params.getArgs().set(args);
                    params.getMetalavaClasspath().set(getMetalavaClasspath());
                }
        );
    }

    private static String asArg(FileCollection files) {
        return files.getFiles().stream().filter(File::exists).map(File::getPath).collect(Collectors.joining(File.pathSeparator));
    }

    private static String asArg(FileSystemLocationProperty<?> file) {
        return file.get().getAsFile().getPath();
    }


}
