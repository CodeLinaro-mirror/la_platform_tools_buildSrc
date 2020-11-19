/*
 * Copyright (C) 2014 The Android Open Source Project
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
package com.android.tools.internal.artifacts.offline

import com.android.tools.internal.artifacts.PomHandler
import com.google.common.collect.Lists
import com.google.common.io.Files
import org.gradle.api.DefaultTask
import org.gradle.api.artifacts.Configuration
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.attributes.Usage
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.TaskAction

/**
 */
public class CopyProjectDependencyTask extends DefaultTask {

    private List<Configuration> entryProjects;

    void setEntryProjectPaths(List<String> entryProjectPaths) {
        entryProjects = new ArrayList<>()
        for (String entryProject: entryProjectPaths) {
            Configuration projectConfiguration = project.configurations.detachedConfiguration()
            // Set runtime usage
            projectConfiguration.attributes.attribute(Usage.USAGE_ATTRIBUTE, project.objects.named(Usage, Usage.JAVA_RUNTIME))
            projectConfiguration.dependencies.add(project.dependencies.project(Collections.singletonMap("path", entryProject)))
            entryProjects.add(projectConfiguration)
        }
    }

    @InputFiles
    List<Configuration> getEntryProject() {
        return entryProjects
    }

    @TaskAction
    void copy() {
        List componentIds = Lists.newArrayList()

        for (Configuration conf: entryProjects) {
            // select transitive dependencies, from this configuration and all referenced subprojects.
            componentIds += conf.incoming.resolutionResult.allDependencies.findAll {
                it.selected.id instanceof ModuleComponentIdentifier
            }
        }

        // remove duplicates.
        componentIds = componentIds.unique { dep1, dep2 ->
            dep1.selected.id.displayName <=> dep2.selected.id.displayName
        }

        def repoDir = new File(project.rootDir.parentFile, 'prebuilts/tools/common/m2/repository')

        componentIds.each { dep ->
            ModuleComponentIdentifier componentId = dep.selected.id
            makeOfflineCopyFor(componentId.group, componentId.module, componentId.version, repoDir, true)
        }
    }

    protected void makeOfflineCopyFor(String group, String module, String version, File repoDir, boolean copyNoticeOrLicense) {
        def artifactPath = "${group.replace('.' as char, File.separatorChar)}${File.separatorChar}${module}${File.separatorChar}${version}"

        def artifactFolder = new File(repoDir, artifactPath)

        // find the jar file.
        def srcFile = new File(artifactFolder, "${module}-${version}.jar")

        File destinationFolder = new File(project.ext.offlineRepo, artifactPath)
        destinationFolder.mkdirs()

        if (srcFile.isFile()) {
            Files.copy(srcFile, new File(destinationFolder, srcFile.getName()))
        }

        // find the pom file.
        srcFile = new File(artifactFolder, "${module}-${version}.pom")
        if (srcFile.isFile()) {
            Files.copy(srcFile, new File(destinationFolder, srcFile.getName()))
        }

        // search for a parent pom.
        def pomHandler = new PomHandler(srcFile)
        def parentPomId = pomHandler.parentPom
        if (parentPomId != null) {
            makeOfflineCopyFor(parentPomId.group, parentPomId.name, parentPomId.version, repoDir, false)
        }

        if (copyNoticeOrLicense) {
            for (noticeOrLicenseFileName in ['NOTICE', 'LICENSE']) {
                srcFile = new File(artifactFolder, noticeOrLicenseFileName)
                String artifactPathParts = artifactPath
                while (!srcFile.isFile() && artifactPathParts.indexOf(File.separator) > 0) {
                    // Walk up the containing directories looking for a shared notice file.
                    artifactPathParts = artifactPathParts.substring(0, artifactPathParts.lastIndexOf(File.separator))
                    srcFile = new File(new File(repoDir, artifactPathParts), noticeOrLicenseFileName)
                }
                if (srcFile.isFile()) {
                    Files.copy(srcFile, new File(new File(project.ext.offlineRepo, artifactPathParts), noticeOrLicenseFileName))
                    return
                }
            }
            throw new RuntimeException("Missing NOTICE or LICENSE file for: " + artifactFolder)
        }
    }
}
