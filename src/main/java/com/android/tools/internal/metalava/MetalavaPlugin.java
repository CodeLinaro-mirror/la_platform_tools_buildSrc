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

import org.gradle.api.Action;
import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.artifacts.Configuration;
import org.gradle.api.file.CopySpec;
import org.gradle.api.plugins.JavaBasePlugin;
import org.gradle.api.tasks.SourceSet;
import org.gradle.api.tasks.SourceSetContainer;
import org.gradle.api.tasks.TaskProvider;
import org.gradle.api.tasks.bundling.Zip;
import org.gradle.internal.jvm.Jvm;

import java.io.File;


public class MetalavaPlugin implements Plugin<Project> {

    private static final String METALAVA_MAVEN = "com.android.tools.metalava:metalava:1.0.0-alpha04";


    @Override
    public void apply(Project project) {
        Configuration metalavaClasspath =
                project.getConfigurations().detachedConfiguration(
                        project.getDependencies().create(METALAVA_MAVEN)
                );

        project.getPlugins().withType(JavaBasePlugin.class, javaBasePlugin -> {
            TaskProvider<GenerateApiTask> generateApi = project.getTasks().register("generateApi", GenerateApiTask.class, task -> {
                SourceSetContainer sourceSets = project.getExtensions().getByType(SourceSetContainer.class);
                SourceSet main = sourceSets.getByName(SourceSet.MAIN_SOURCE_SET_NAME);
                task.getSourcePaths().from(main.getAllJava().getSourceDirectories());
                task.getSourcePaths().disallowChanges();
                task.getJdkHome().set(Jvm.current().getJavaHome());
                task.getJdkHome().disallowChanges();
                task.getClasspath().from(main.getCompileClasspath());
                task.getClasspath().disallowChanges();
                task.getMetalavaClasspath().from(metalavaClasspath);
                task.getMetalavaClasspath().disallowChanges();
                task.getOutputDirectory().set(project.getLayout().getBuildDirectory().dir("metalava/current"));
                task.getOutputDirectory().disallowChanges();
            });

            TaskProvider<Zip> zip = project.getTasks().register("distMetalavaApiZip", Zip.class, task -> {
                task.from(
                        generateApi.flatMap(GenerateApiTask::getOutputDirectory),
                        copySpec -> copySpec.rename(path -> "current/" + path)
                );
                File dist = new File(project.getRootProject().getExtensions().getExtraProperties().get("androidHostDist").toString());
                task.getDestinationDirectory().set(dist);
                task.getArchiveFileName().set("apis.zip");
            });
        });
    }
}
