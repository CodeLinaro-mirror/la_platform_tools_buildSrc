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

import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.artifacts.Configuration;
import org.gradle.api.attributes.Category;
import org.gradle.api.attributes.Usage;
import org.gradle.api.file.Directory;
import org.gradle.api.file.RegularFile;
import org.gradle.api.plugins.JavaBasePlugin;
import org.gradle.api.provider.Provider;
import org.gradle.api.tasks.JavaExec;
import org.gradle.api.tasks.SourceSet;
import org.gradle.api.tasks.SourceSetContainer;
import org.gradle.api.tasks.Sync;
import org.gradle.api.tasks.TaskContainer;
import org.gradle.api.tasks.TaskProvider;
import org.gradle.api.tasks.bundling.Zip;
import org.gradle.api.tasks.testing.Test;
import org.gradle.internal.jvm.Jvm;
import java.io.File;

public class MetalavaPlugin implements Plugin<Project> {

    private static final String METALAVA_MAVEN = "com.android.tools.metalava:metalava:1.0.0-alpha14";
    protected static final String API_LEVELS_FILE = "apiLevels.json";

    @Override
    public void apply(Project project) {
        Configuration metalavaClasspath =
                project.getConfigurations().detachedConfiguration(
                        project.getDependencies().create(METALAVA_MAVEN)
                );
        metalavaClasspath.setCanBeConsumed(false);
        metalavaClasspath.setCanBeResolved(true);

        project.getPlugins().withType(JavaBasePlugin.class, javaBasePlugin -> {
            SourceSetContainer sourceSets = project.getExtensions().getByType(SourceSetContainer.class);
            SourceSet main = sourceSets.getByName(SourceSet.MAIN_SOURCE_SET_NAME);
            TaskContainer tasks = project.getTasks();
            String buildVersion = project.getRootProject().getExtensions().getExtraProperties().get("releaseBuildVersion").toString();

            TaskProvider<GenerateOldApisTask> generateOldApiTask = tasks.register("generateOldApis", GenerateOldApisTask.class, task -> {
                task.getJdkHome().set(Jvm.current().getJavaHome());
                task.getJdkHome().disallowChanges();
                task.getClasspath().from(main.getCompileClasspath());
                task.getClasspath().disallowChanges();
                task.getMetalavaClasspath().from(metalavaClasspath);
                task.getMetalavaClasspath().disallowChanges();
                task.getOutputDirectory().set(project.getLayout().getProjectDirectory().dir("previous-gradle-apis"));
                task.getOutputDirectory().disallowChanges();
            });

            TaskProvider<GenerateApiTask> generateApi = tasks.register("generateApi", GenerateApiTask.class, task -> {
                task.getSourcePaths().from(main.getAllJava().getSourceDirectories());
                task.getSourcePaths().disallowChanges();
                task.getJdkHome().set(Jvm.current().getJavaHome());
                task.getJdkHome().disallowChanges();
                task.getProjectApiDirectory().set(project.getLayout().getProjectDirectory().dir("previous-gradle-apis"));
                task.getProjectApiDirectory().disallowChanges();
                task.getAgpVersion().set(buildVersion);
                task.getAgpVersion().disallowChanges();
                task.getClasspath().from(main.getCompileClasspath());
                task.getClasspath().disallowChanges();
                task.getMetalavaClasspath().from(metalavaClasspath);
                task.getMetalavaClasspath().disallowChanges();
                task.getOutputDirectory().set(project.getLayout().getBuildDirectory().dir("metalava/current"));
                task.getOutputDirectory().disallowChanges();
            });

            Provider<Directory> generateApiOutput = generateApi.flatMap(GenerateApiTask::getOutputDirectory);

            // new outgoing variant for the metalava generated API metadata json files
            Configuration versionsMetadataConfiguration = project.getConfigurations().create("versionsMetadata", configuration -> {
                configuration.setCanBeConsumed(true);
                configuration.setCanBeResolved(false);

                configuration.attributes(attributes -> {
                    attributes.attribute(Usage.USAGE_ATTRIBUTE, project.getObjects().named(Usage.class, Usage.JAVA_RUNTIME));
                    attributes.attribute(Category.CATEGORY_ATTRIBUTE, project.getObjects().named(Category.class, "metalava-api-levels"));
                });

                // Add the version metadata json file as an artifact
                Provider<RegularFile> apiLevelsFile =
                    generateApi.map(task -> task.getOutputDirectory().file(API_LEVELS_FILE).get());
                configuration.getOutgoing().artifact(apiLevelsFile);
            });

            tasks.register("generateApiReleaseNotes", GenerateApiReleaseNotes.class, task -> {
                task.getInputDirectory().set(generateApiOutput);
                task.getInputDirectory().disallowChanges();
                task.getOldApiFiles().from(project.getLayout().getProjectDirectory().dir("previous-gradle-apis").getAsFileTree());
                task.getOldApiFiles().disallowChanges();
                task.getOutputDirectory().set(project.getLayout().getBuildDirectory().dir("metalava/reports"));
                task.getOutputDirectory().disallowChanges();
                task.getAgpBuildVersion().set(buildVersion);
                task.getAgpBuildVersion().disallowChanges();
            });

            tasks.register("distMetalavaApiZip", Zip.class, task -> {
                task.from(generateApiOutput, copySpec -> copySpec.rename(path -> "current/" + path));
                File dist = new File(project.getRootProject().getExtensions().getExtraProperties().get("androidHostDist").toString());
                task.getDestinationDirectory().set(dist);
                task.getArchiveFileName().set("apis.zip");
            });

            SourceSet metalavaTestSourceSet = sourceSets.create("metalavaTest");
            metalavaTestSourceSet.getResources().srcDir("api");

            MetaLavaTestInputs metalavaCurrentTxtInput = project.getObjects().newInstance(MetaLavaTestInputs.class);
            metalavaCurrentTxtInput.getMetalavaCurrentApiFile().set(generateApiOutput);

            tasks.register("metalavaTest", Test.class, task -> {
                task.setTestClassesDirs(metalavaTestSourceSet.getOutput().getClassesDirs());
                task.setClasspath(metalavaTestSourceSet.getRuntimeClasspath());
                task.getJvmArgumentProviders().add(metalavaCurrentTxtInput);
            });

            tasks.register("updateMetalavaApi", Sync.class, task -> {
                task.from(generateApiOutput);
                task.setDestinationDir(project.file("api"));
                // exclude checking in apiLevels.json
                task.exclude("**/" + API_LEVELS_FILE);
            });

            tasks.withType(JavaExec.class).configureEach(task -> {
                task.getJvmArgumentProviders().add(metalavaCurrentTxtInput);
            });
        });
    }
}
