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

package com.android.tools.internal.dackka;

import com.google.gson.annotations.SerializedName;
import java.io.File;
import java.util.List;

import org.gradle.api.file.FileCollection;
import org.gradle.api.tasks.Classpath;
import org.gradle.api.tasks.Input;
import org.gradle.api.tasks.InputDirectory;
import org.gradle.api.tasks.InputFiles;
import org.gradle.api.tasks.Nested;
import org.gradle.api.tasks.PathSensitive;
import org.gradle.api.tasks.PathSensitivity;

public class DackkaInputModel {

    /**
     * <a href="https://kotlinlang.org/docs/dokka-cli.html#source-set-configuration">dokka source set config</a>
     */
    static class SourceSet {
        @Input
        String displayName;
        @Nested
        @SerializedName("sourceSetID")
        SourceSetId id;
        @Classpath
        FileCollection classpath;
        @InputFiles
        @PathSensitive(PathSensitivity.RELATIVE)
        FileCollection sourceRoots;
        @InputFiles
        @PathSensitive(PathSensitivity.RELATIVE)
        FileCollection samples;
        @InputFiles
        @PathSensitive(PathSensitivity.RELATIVE)
        FileCollection includes;
        @Input
        String analysisPlatform;
        @Input
        List<String> documentedVisibilities;
        @Input
        Boolean noJdkLink;
        @Input
        Boolean noAndroidSdkLink;
        @Input
        Boolean noStdlibLink;
        @Nested
        List<SourceSetId> dependentSourceSets;
        @Nested
        List<GlobalDocsLink> externalDocumentationLinks;
        @Nested
        List<SrcLink> sourceLinks;

        public SourceSet(String displayName, SourceSetId id, FileCollection classpath, FileCollection sourceRoots, FileCollection samples, FileCollection includes, String analysisPlatform, List<String> documentedVisibilities, Boolean noJdkLink, Boolean noAndroidSdkLink, Boolean noStdlibLink, List<SourceSetId> dependentSourceSets, List<GlobalDocsLink> externalDocumentationLinks, List<SrcLink> sourceLinks) {
            this.displayName = displayName;
            this.id = id;
            this.classpath = classpath;
            this.sourceRoots = sourceRoots;
            this.samples = samples;
            this.includes = includes;
            this.analysisPlatform = analysisPlatform;
            this.documentedVisibilities = documentedVisibilities;
            this.noJdkLink = noJdkLink;
            this.noAndroidSdkLink = noAndroidSdkLink;
            this.noStdlibLink = noStdlibLink;
            this.dependentSourceSets = dependentSourceSets;
            this.externalDocumentationLinks = externalDocumentationLinks;
            this.sourceLinks = sourceLinks;
        }
    }

    static class SourceSetId {
        @Input String sourceSetName;
        @Input String scopeId;

        public SourceSetId(String sourceSetName, String scopeId) {
            this.sourceSetName = sourceSetName;
            this.scopeId = scopeId;
        }
    }

    /**
     * <a href="https://kotlinlang.org/docs/dokka-cli.html#source-link-configuration">dokka source link config</a>
     */
    static class SrcLink {
        @InputDirectory @PathSensitive(PathSensitivity.RELATIVE)
        File localDirectory;
        @Input
        String remoteUrl;
        @Input
        String remoteLineSuffix;
    }

    static class GlobalDocsLink {
        @Input
        String url;
        @Input
        String packageListUrl;

        public GlobalDocsLink(String url, String packageListUrl) {
            this.url = url;
            this.packageListUrl = packageListUrl;
        }
    }
}