/*
 * Copyright (C) 2019 The Android Open Source Project
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

package com.android.tools.internal.artifacts.offline;

import java.io.IOException;
import org.gradle.api.DefaultTask;
import org.gradle.api.Project;
import org.gradle.api.file.RegularFileProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.Input;
import org.gradle.api.tasks.OutputFile;
import org.gradle.api.tasks.TaskAction;
import org.gradle.api.tasks.TaskProvider;

public abstract class ReadmeExtractorTask extends DefaultTask {

    @Input
    public abstract Property<String> getAgpVersion();

    @OutputFile
    public abstract RegularFileProperty getOutputZip();

    @TaskAction
    public void extractReadme() throws IOException {
        OfflineRepoUtils.extractReadme(
                getOutputZip().get().getAsFile().toPath(), getAgpVersion().get());
    }

    public static TaskProvider<ReadmeExtractorTask> create(Project project) {
        return project.getTasks().register("extractOfflineRepoReadme", ReadmeExtractorTask.class, task -> {
            task.getAgpVersion().set( project.getProviders().provider(() ->
                    (String) project.getRootProject().getExtensions().getExtraProperties().get("buildVersion")));
            task.getOutputZip().set(project.getLayout().getBuildDirectory().file("intermediates/offline-repo-readme.zip"));
        });
    }
}
