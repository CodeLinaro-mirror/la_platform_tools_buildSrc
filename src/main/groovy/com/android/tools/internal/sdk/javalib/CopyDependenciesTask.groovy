/*
 * Copyright (C) 2013 The Android Open Source Project
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

package com.android.tools.internal.sdk.javalib

import com.android.tools.internal.BaseTask
import com.google.common.base.Charsets
import com.google.common.base.Joiner
import com.google.common.collect.Lists
import com.google.common.io.CharStreams
import com.google.common.io.Files
import org.gradle.api.GradleException
import org.gradle.api.artifacts.Configuration
import org.gradle.api.artifacts.ModuleVersionIdentifier
import org.gradle.api.artifacts.ResolvedArtifact
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction

import java.util.zip.ZipFile
import java.util.zip.ZipEntry

class CopyDependenciesTask extends BaseTask {

    @OutputDirectory
    File outputDir

    @InputFiles
    Configuration getInputFiles() {
        return project.configurations.runtime
    }

    @OutputDirectory
    File noticeDir

    @InputDirectory
    File repoDir

    @TaskAction
    public void copyDependencies() {
        File depOutDir = getOutputDir()
        depOutDir.deleteDir()
        depOutDir.mkdirs()

        File noticeOutDir = getNoticeDir()
        noticeOutDir.deleteDir()
        noticeOutDir.mkdirs()

        Configuration configuration = project.configurations.runtime
        Set<ResolvedArtifact> artifacts = configuration.resolvedConfiguration.resolvedArtifacts

        StringBuilder sb = new StringBuilder()
        for (ResolvedArtifact artifact : artifacts) {
            sb.setLength(0)
            sb.append("${artifact.moduleVersion.id.toString()} > ")

            try {
                ModuleVersionIdentifier id = artifact.moduleVersion.id
                // Make sure it's not:
                // - Android artifact (unless it's an external one since we do want to package those)
                // - A local artifact (ie a sub-project, those are copied on their own)
                // - A invalid artifact (non jar packaging)
                if (isAndroidArtifact(id) && !isAndroidExternalArtifact(id)) {
                    sb.append("SKIPPED (android)")
                } else if (isLocalArtifact(id)) {
                    sb.append("SKIPPED (local)")
                } else if (!isValidArtifactType(artifact)) {
                    sb.append("SKIPPED (type = ${artifact.type})")
                } else {

                    // copy the artifact
                    File dest = new File(depOutDir, artifact.file.name)
                    sb.append(dest.absolutePath)
                    Files.copy(artifact.file, dest)

                    // copy the license file
                    Reader noticeReader = null;

                    // first look in the jar itself
                    try {
                        ZipFile contents = new ZipFile(artifact.file)
                        ZipEntry noticeEntry = contents.getEntry('NOTICE')
                        if (noticeEntry != null) {
                            System.out.
                                    println("getting from zip " + artifact.file.getAbsolutePath())
                            noticeReader =
                                    new InputStreamReader(contents.getInputStream(noticeEntry))
                        }
                    } catch (Exception ignore) {}

                    // otherwise look in the repo dir
                    if (noticeReader == null) {
                        File artifactDir = new File(
                                new File((String) id.group.replace('.', File.separator), id.name),
                                id.version)
                        File fromFile = new File(new File(repoDir, artifactDir.getPath()), 'NOTICE')

                        while (!fromFile.isFile()) {
                            // Walk up the containing directories looking for a shared notice file.
                            artifactDir = artifactDir.getParentFile();
                            if (artifactDir == null) {
                                break;
                            }
                            fromFile = new File(new File(repoDir, artifactDir.getPath()), 'NOTICE')
                        }
                        if (fromFile.isFile()) {
                            noticeReader = new FileReader(fromFile)
                        }
                    }
                    if (noticeReader == null) {
                        sb.append("Error: Missing NOTICE file")
                        throw new GradleException(
                                "Missing NOTICE file for " + artifact.file)
                    }

                    File toFile = new File(noticeOutDir, "NOTICE_" + artifact.file.name + ".txt")

                    sb.append(" (${toFile.absolutePath})")

                    copyNoticeAndAddHeader(noticeReader, toFile, "lib/${artifact.file.name}")

                }
            } finally {

            }

            logger.info(sb.toString())
        }
    }

    private static void copyNoticeAndAddHeader(Reader from, File to, String name) {
        List<String> lines = CharStreams.readLines(from)
        List<String> noticeLines = Lists.newArrayListWithCapacity(lines.size() + 4)
        noticeLines.addAll([
                "============================================================",
                "Notices for file(s):",
                name,
                "------------------------------------------------------------"
        ]);
        noticeLines.addAll(lines);

        Files.asCharSink(to, Charsets.UTF_8).write(Joiner.on("\n").join(noticeLines.iterator()))
    }
}
