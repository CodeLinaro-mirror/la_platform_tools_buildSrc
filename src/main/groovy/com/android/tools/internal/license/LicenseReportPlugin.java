/*
 * Copyright (C) 2018 The Android Open Source Project
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
package com.android.tools.internal.license;

import org.gradle.api.Plugin;
import org.gradle.api.Project;
import org.gradle.api.provider.ListProperty;

import java.io.File;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Plugin setting up the licenseReport task.
 */
public class LicenseReportPlugin implements Plugin<Project> {
    @Override
    public void apply(Project project) {

        File outputFile = new File(
                (File) project.getRootProject().getExtensions().getExtraProperties().get("androidHostDist"),
                "license-" + project.getName() + ".txt");

        //noinspection unchecked
        ListProperty<String> ignoredDependencies = project.getObjects().listProperty(String.class);

        project.getTasks().create("licenseReport", ReportTask.class, task -> {
            task.setRuntimeDependencies(project.getConfigurations().getByName("runtimeClasspath"));
            task.setIgnoredDependencies(ignoredDependencies);
            task.setOutputFile(outputFile);
        });

        project.getExtensions().create("licenseReport", LicenseReportExtension.class, ignoredDependencies);
    }

    public static class LicenseReportExtension {
        private final ListProperty<String> ignoredDependencies;

        public LicenseReportExtension(ListProperty<String> ignoredDependencies) {
            this.ignoredDependencies = ignoredDependencies;
            this.ignoredDependencies.set((List<String>)Collections.EMPTY_LIST);
        }

        public void setIgnored(String value) {
            setIgnored(Collections.singletonList(value));
        }

        public void setIgnored(String... values) {
            setIgnored(Arrays.asList(values));
        }

        public void setIgnored(List<String> values) {
            ignoredDependencies.set(values);
        }
    }
}
