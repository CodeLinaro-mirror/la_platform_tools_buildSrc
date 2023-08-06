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

package com.android.tools.internal.dackka;

import groovy.util.Eval;
import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.Transformer;
import org.gradle.api.artifacts.Configuration;
import org.gradle.api.artifacts.ConfigurationContainer;
import org.gradle.api.attributes.Category;
import org.gradle.api.attributes.DocsType;
import org.gradle.api.attributes.Usage;
import org.gradle.api.provider.Provider;
import org.gradle.api.tasks.TaskProvider;
import org.gradle.api.tasks.bundling.Zip;

import java.io.File;

public class DackkaPlugin implements Plugin<Project> {
    @Override
    public void apply(Project project) {

        Object cloneArtifactsExtension = project.getRootProject().getExtensions().getByName("cloneArtifacts");

        project.getRepositories().add(
                project.getRepositories().maven(maven ->
                        maven.setUrl(Eval.x(cloneArtifactsExtension, "x.repository"))
        ));

        project.getPlugins().apply("java-base");

        String buildVersion = project.getRootProject().getExtensions().getExtraProperties().get("buildVersion").toString();
        String devsitePrefix = "tools/gradle-api/" +  buildVersion.substring(0, buildVersion.lastIndexOf("."));


        ConfigurationContainer configurations = project.getConfigurations();
        Configuration api = configurations.create("api", it -> {
            it.setCanBeConsumed(false);
            it.setCanBeResolved(false);
        });
        Configuration provided = configurations.create("provided", it -> {
            it.setCanBeConsumed(false);
            it.setCanBeResolved(false);
        });

        Configuration sources = configurations.create("sources", it -> {
            it.setCanBeConsumed(false);
            it.setCanBeResolved(true);
            it.extendsFrom(api);
            it.setTransitive(false);
            it.attributes(attributes -> {
                attributes.attribute(Usage.USAGE_ATTRIBUTE, project.getObjects().named(Usage.class, Usage.JAVA_RUNTIME));
                attributes.attribute(Category.CATEGORY_ATTRIBUTE, project.getObjects().named(Category.class, Category.DOCUMENTATION));
                attributes.attribute(DocsType.DOCS_TYPE_ATTRIBUTE, project.getObjects().named(DocsType.class, DocsType.SOURCES));
            });
        });

        Configuration compileClasspath = configurations.create("compileClasspath", it -> {
            it.setCanBeConsumed(false);
            it.setCanBeResolved(true);
            it.extendsFrom(api, provided);
            it.setTransitive(true);
            it.attributes(attributes -> {
                attributes.attribute(Usage.USAGE_ATTRIBUTE, project.getObjects().named(Usage.class, Usage.JAVA_API));
            });
        });

        TaskProvider<DackkaTask> dackkaTask = project.getTasks().register("dackkaDocs", DackkaTask.class, task -> {
            task.getDevsiteTenant().set(devsitePrefix);
            task.getDestinationDirectory().set(project.getLayout().getBuildDirectory().dir("dackka"));
            task.getDackkaClasspath().from(project.getRootProject().file("../prebuilts/tools/common/dackka/dackka-1.3.6.jar"));
            task.getDependenciesClasspath().from(compileClasspath);
            task.getSources().from(sources);
            task.getExtractedSources().set(project.getLayout().getBuildDirectory().dir("intermediates/extracted_sources"));
            task.getPackageListsDirectory().set(project.getRootProject().file("../prebuilts/tools/common/dackka/package-lists"));
        });


        Provider<WriteDocsVersionFileTask> writeVersions = project.getTasks().register("writeDocsVersionFile", WriteDocsVersionFileTask.class, task -> {
            task.getBuildVersion().set(buildVersion);
            task.getOutputDirectory().set(project.getLayout().getBuildDirectory().dir("dokkaVersion"));
        });

        project.getTasks().register("dackkaZip", Zip.class, dakkaZip -> {
            // The consumption script expects the reference/tools/gradle-api/<version>/ prefix to be removed
            dakkaZip.from(dackkaTask.flatMap(it -> it.getDestinationDirectory().dir("reference/" + devsitePrefix) ));
            dakkaZip.from(writeVersions.flatMap(WriteDocsVersionFileTask::getOutputDirectory));
            dakkaZip.getDestinationDirectory().set((File)project.getRootProject().getExtensions().getExtraProperties().get("androidHostDist"));
            dakkaZip.getArchiveFileName().set("documentation.zip");
        });

    }
}