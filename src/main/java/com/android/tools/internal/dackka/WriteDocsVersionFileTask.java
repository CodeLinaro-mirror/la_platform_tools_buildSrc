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

import org.gradle.api.DefaultTask;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.Input;
import org.gradle.api.tasks.OutputDirectory;
import org.gradle.api.tasks.TaskAction;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

public abstract class WriteDocsVersionFileTask extends DefaultTask {
    @Input
    public abstract Property<String> getBuildVersion();
    @OutputDirectory
    public abstract DirectoryProperty getOutputDirectory();

    @TaskAction
    public void writeFile() throws IOException {
        Path outputDir = getOutputDirectory().get().getAsFile().toPath();
        DackkaTask.DackkaWorkAction.clean(outputDir);
        Path versionFile = outputDir.resolve("version.properties");

        String docsVersion = getBuildVersion().get().substring(0, getBuildVersion().get().lastIndexOf("."));
        Properties properties = new Properties();
        properties.put("androidGradlePluginVersion", getBuildVersion().get());
        properties.put("docsVersion", docsVersion);
        try(BufferedWriter writer = Files.newBufferedWriter(versionFile)) {
            properties.store(writer, null);
        }
    }
}


