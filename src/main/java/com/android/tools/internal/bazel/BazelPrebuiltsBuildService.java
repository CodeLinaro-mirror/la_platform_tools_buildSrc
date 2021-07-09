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

import org.gradle.api.file.Directory;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.logging.Logging;
import org.gradle.api.provider.MapProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.provider.Provider;
import org.gradle.api.services.BuildService;
import org.gradle.api.services.BuildServiceParameters;
import org.gradle.process.ExecOperations;

import javax.annotation.concurrent.GuardedBy;
import javax.annotation.concurrent.ThreadSafe;
import javax.inject.Inject;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

@ThreadSafe
public abstract class BazelPrebuiltsBuildService implements BuildService<BazelPrebuiltsBuildService.Params>, AutoCloseable {

    public BazelPrebuiltsBuildService() {
        Logging.getLogger(BazelPrebuiltsBuildService.class).lifecycle("Using experimental hybrid build");
    }

    public interface Params extends BuildServiceParameters {
        DirectoryProperty getRootDir();
        MapProperty<String, String> getSubstitutions();
    }

    @Inject
    public abstract ExecOperations getExecOperations();

    @GuardedBy("this")
    private boolean invoked = false;

    public synchronized void ensurePrebuiltsAreBuilt() {
        if (invoked) {
            return;
        }

        try {
            invokeBazel();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }

        invoked = true;
    }

    public Provider<Directory> getMavenRepoLocation() {
        return getParameters()
                .getRootDir()
                .dir("../out/build/base/agp_artifacts_zip.zip");
    }

    private void invokeBazel() throws IOException {
        getExecOperations()
                .exec(
                        spec -> {
                            spec.executable(
                                    getParameters()
                                            .getRootDir()
                                            .file("base/bazel/bazel")
                                            .get()
                                            .getAsFile());
                            spec.args(
                                    "build",
                                    "//tools/base:agp_artifacts_zip");
                        });
        // Unzip: TODO - can this be optimized a bit, as it runs every build.
        Path zip =
                getParameters()
                        .getRootDir()
                        .file(
                                "../bazel-bin/tools/base/agp_artifacts_zip.zip")
                        .get()
                        .getAsFile()
                        .toPath();
        Path directory = getMavenRepoLocation().get().getAsFile().toPath();
        Logging.getLogger(BazelPrebuiltsBuildService.class).lifecycle("Extracting " + zip + " to " + directory);
        Files.createDirectories(directory);
        Files.walkFileTree(
                directory,
                new SimpleFileVisitor<>() {
                    @Override
                    public FileVisitResult visitFile(Path file, BasicFileAttributes attrs)
                            throws IOException {
                        Files.delete(file);
                        return FileVisitResult.CONTINUE;
                    }
                });
        try (ZipInputStream zis =
                     new ZipInputStream(new BufferedInputStream(Files.newInputStream(zip)))) {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    continue;
                }
                Path target = directory.resolve(entry.getName());
                Files.createDirectories(target.getParent());
                Files.copy(zis, target);
            }
        }
    }

    @Override
    public synchronized void close() throws Exception {
        invoked = false;
    }


}
