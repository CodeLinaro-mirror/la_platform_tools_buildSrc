/*
 * Copyright (C) 2023 The Android Open Source Project
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
import org.apache.commons.io.FileUtils;
import org.gradle.api.DefaultTask;
import org.gradle.api.Project;
import org.gradle.api.artifacts.Configuration;
import org.gradle.api.file.ConfigurableFileCollection;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.file.FileCollection;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.Classpath;
import org.gradle.api.tasks.CompileClasspath;
import org.gradle.api.tasks.Input;
import org.gradle.api.tasks.InputDirectory;
import org.gradle.api.tasks.InputFile;
import org.gradle.api.tasks.InputFiles;
import org.gradle.api.tasks.Internal;
import org.gradle.api.tasks.OutputDirectory;
import org.gradle.api.tasks.PathSensitive;
import org.gradle.api.tasks.PathSensitivity;
import org.gradle.api.tasks.TaskAction;
import org.gradle.workers.WorkerExecutor;
import javax.inject.Inject;
import java.io.File;
import java.io.IOException;
import java.util.AbstractMap;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;

/**
 * Task that generates API surface files from older AGP versions by
 * extracting their sources from the prebuilt sources.jar and calling Metalava
 */
public abstract class GenerateOldApisTask extends DefaultTask {
    @Internal
    public abstract DirectoryProperty getJdkHome();

    @CompileClasspath
    public abstract ConfigurableFileCollection getClasspath();

    @Classpath
    public abstract ConfigurableFileCollection getMetalavaClasspath();

    @OutputDirectory
    public abstract DirectoryProperty getOutputDirectory();

    @Inject
    public abstract WorkerExecutor getWorkerExecutor();

    @Input
    abstract Property<String> getGroupId();

    @Input
    abstract Property<String> getArtifactId();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract DirectoryProperty getPrebuiltsDirectory();

    @TaskAction
    public void generateApi() throws IOException {
        FileUtils.cleanDirectory(getOutputDirectory().get().getAsFile());
        String groupId = getGroupId().get();
        String artifactId = getArtifactId().get();
        File prebuiltsDir = getPrebuiltsDirectory().get().getAsFile();
        File projectPrebuiltsDir = new File(prebuiltsDir, groupId.replace(".", "/") + "/" + artifactId);

        List<AbstractMap.SimpleEntry<AgpVersion, File>> allPrebuiltVersions = Arrays.stream(Objects.requireNonNull(projectPrebuiltsDir.list()))
                .map(name -> new File(projectPrebuiltsDir, name))
                .filter(File::isDirectory)
                .map(file -> new AbstractMap.SimpleEntry<>(AgpVersion.parseOrNull(file.getName()), file))
                .filter(pair -> pair.getKey() != null)
                .filter(pair -> pair.getKey().isStable()) // only generate for stable prebuilts
                .sorted(java.util.Map.Entry.comparingByKey(Comparator.reverseOrder()))
                .toList();

        allPrebuiltVersions.forEach(version -> {
            String sourcesJarFileName = artifactId + "-" + version.getKey().toString() + "-sources";
            File sourceJar = new File(version.getValue(), sourcesJarFileName + ".jar");
            if (!sourceJar.exists()) {
                return;
            }
            List<String> args = Lists.newArrayList(
                    "--hide",
                    "DeprecationMismatch",
                    "--source-path",
                    TaskUtils.asArg(getSources(getProject(), sourceJar, sourcesJarFileName)),
                    "--format=v4",
                    "--warnings-as-errors",
                    "--jdk-home",
                    TaskUtils.asArg(getJdkHome()),
                    "--classpath",
                    TaskUtils.asArg(getClasspath()),
                    "--api",
                    TaskUtils.asArg(getOutputDirectory()) + "/" + version.getKey().toString() + ".txt",
                    "--verbose"
            );

            getWorkerExecutor().noIsolation().submit(
                    MetalavaWorkAction.class,
                    params -> {
                        params.getArgs().set(args);
                        params.getMetalavaClasspath().set(getMetalavaClasspath());
                        params.getK2UastEnabled().set(true);
                    }
            );
        });
    }

    private FileCollection getSources(Project project, File jar, String fileName) {
        Configuration configuration = project.getConfigurations().detachedConfiguration(
                project.getDependencies().create(project.files(jar))
        );
        configuration.setTransitive(false);

        File unzippedDir = new File(
            project.getLayout().getBuildDirectory().getAsFile().get().getPath() + "/unzipped-sources/" + fileName
        );
        project.copy(copySpec -> {
            copySpec.from(project.zipTree(configuration.getSingleFile()));
            copySpec.into(unzippedDir);
        });
        return project.files(unzippedDir);
    }
}
