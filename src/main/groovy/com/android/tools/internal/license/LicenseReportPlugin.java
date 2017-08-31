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
import org.gradle.api.provider.PropertyState;

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
        PropertyState<List<String>> whiteListedDependencies = project.property((Class<List<String>>)(Class<?>)List.class);

        project.getTasks().create("licenseReport", ReportTask.class, task -> {
            task.setRuntimeDependencies(project.getConfigurations().getByName("runtimeClasspath"));
            task.setWhiteListedDependencies(whiteListedDependencies);
            task.setOutputFile(outputFile);
        });

        project.getExtensions().create("licenseReport", LicenseReportExtension.class, whiteListedDependencies);
    }

    public static class LicenseReportExtension {
        private final PropertyState<List<String>> whiteListedDependencies;

        public LicenseReportExtension(PropertyState<List<String>> whiteListedDependencies) {
            this.whiteListedDependencies = whiteListedDependencies;
            this.whiteListedDependencies.set((List<String>)Collections.EMPTY_LIST);
        }

        public void setWhiteList(String value) {
            setWhiteList(Collections.singletonList(value));

        }

        public void setWhiteListe(String... values) {
            setWhiteList(Arrays.asList(values));
        }

        public void setWhiteList(List<String> values) {
            whiteListedDependencies.set(values);
        }
    }
}
