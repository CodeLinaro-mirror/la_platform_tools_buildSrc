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

package com.android.tools.internal.bazel;

import com.google.common.collect.ImmutableMap;
import org.gradle.api.InvalidUserDataException;
import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.logging.Logging;
import org.gradle.api.provider.ProviderFactory;

import java.io.IOException;
import java.io.StringReader;
import java.io.UncheckedIOException;
import java.util.Map;
import java.util.Properties;

/**
 * Plugin to handle hybrid build dependency substitutions.
 *
 * This will be simplified once hybrid-build is enabled by default and the projects that are built in Bazel
 * are no longer modelled in Gradle. The only thing that will remain is the beforeResolve hook for invoking
 * bazel which can be moved to a settings plugin or similar.
 */
public class BazelPrebuiltSupportPlugin implements Plugin<Project> {
    @Override
    public void apply(Project project) {
        /*
         This property can be set by AGP developers to try out the hybrid build support locally before it becomes the
         default.
        */
        boolean hybridBuild = getBoolean(project.getProviders(), "hybrid-build");
        /*
         This property is set when building within a bazel genrule - in that case all the prerequisites are
         already built an available in the injected repo.
        */
        boolean embeddedBuild = getBoolean(project.getProviders(), "hybrid-build-embedded-in-bazel");
        boolean shouldPublish = true;

        if (hybridBuild || embeddedBuild) {
            BazelPrebuiltsBuildService buildService =
                    project.getGradle()
                            .getSharedServices()
                            .registerIfAbsent(
                                    "BazelInvoker",
                                    BazelPrebuiltsBuildService.class,
                                    spec -> {
                                        spec.parameters(
                                                params -> {
                                                    params.getRootDir().set(project.getRootDir());
                                                    params.getSubstitutions().set(
                                                            project.getProviders()
                                                                    .fileContents(project.getLayout().getProjectDirectory().file(project.getRootDir() + "/base/build-system/artifacts-built-with-bazel.properties"))
                                                                    .getAsText().forUseAtConfigurationTime()
                                                                    .map(it -> parseProjectListingFile(it))
                                                    );

                                                });
                                    })
                            .get();
            if (buildService.getParameters().getSubstitutions().get().containsKey(project.getPath())) {
                shouldPublish = false;
            }
            String baseVersion =
                    project.getRootProject().getExtensions()
                            .getExtraProperties().get("baseVersion").toString();
            String buildVersion =
                    project.getRootProject().getExtensions()
                            .getExtraProperties().get("buildVersion").toString();


            project.getConfigurations()
                    .all(
                            configuration -> {
                                if (hybridBuild) {
                                    configuration
                                            .getIncoming()
                                            .beforeResolve(
                                                    resolvableDependencies -> {
                                                        buildService.ensurePrebuiltsAreBuilt();
                                                    });
                                }
                                configuration.getResolutionStrategy().dependencySubstitution(substitutions -> {
                                    buildService.getParameters().getSubstitutions().get().forEach((gradle, external) ->
                                            {
                                                substitutions.substitute(substitutions.project(gradle))
                                                        .because(external + " is now built with bazel")
                                                        .with(substitutions.module(external.replace("baseVersion", baseVersion).replace("buildVersion", buildVersion)));
                                            }
                                    );
                                });
                            });
            if (hybridBuild) {
                project.getRepositories()
                        .maven(
                                mavenArtifactRepository -> {
                                    mavenArtifactRepository.setUrl(buildService.getMavenRepoLocation());
                                    mavenArtifactRepository.setName("Maven artifacts built by bazel");
                                });
            }
        }

        project.getExtensions().create(
                BazelPrebuiltSupportExtension.class,
                "bazelPrebuilts",
                BazelPrebuiltSupportExtensionImpl.class,
                shouldPublish);

    }

    private Map<String, String> parseProjectListingFile(String content) {
        Properties properties = new Properties();
        try {
            properties.load(new StringReader(content));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        ImmutableMap.Builder<String, String> builder = ImmutableMap.builder();
        properties.forEach((key, value) -> builder.put(key.toString(), value.toString()));
        return builder.build();
    }


    private static boolean getBoolean(ProviderFactory providerFactory, String property) {
        String value =
                providerFactory
                        .gradleProperty(property)
                        .forUseAtConfigurationTime()
                        .getOrElse("false")
                        .trim();

        if (value.equalsIgnoreCase("false")) {
            return false;
        } else if (value.equalsIgnoreCase("true")) {
            return true;
        } else {
            throw new InvalidUserDataException(
                    "expected gradle property "
                            + property
                            + " to be 'true' or 'false', but was '"
                            + value
                            + "'");
        }
    }

}
