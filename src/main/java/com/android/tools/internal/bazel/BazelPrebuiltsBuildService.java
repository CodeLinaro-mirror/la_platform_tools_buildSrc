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
import java.io.IOException;
import java.io.UncheckedIOException;

@ThreadSafe
public abstract class BazelPrebuiltsBuildService implements BuildService<BazelPrebuiltsBuildService.Params>, AutoCloseable {

    public BazelPrebuiltsBuildService() {
        Logging.getLogger(BazelPrebuiltsBuildService.class).lifecycle("Using experimental hybrid build");
    }

    public interface Params extends BuildServiceParameters {
        DirectoryProperty getRootDir();
        MapProperty<String, String> getSubstitutions();
        Property<String> getOsName();
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
                .dir("../out/repo");
    }

    private void invokeBazel() throws IOException {

        getExecOperations()
                .exec(
                        spec -> {
                            spec.executable(
                                    getParameters()
                                            .getRootDir()
                                            .file(getBazelExe())
                                            .get()
                                            .getAsFile());
                            spec.args(
                                    "run",
                                    "//tools/base:agp_artifacts_dir",
                                    "--",
                                    getMavenRepoLocation().get().getAsFile().getAbsolutePath());
                        });
    }

    private String getBazelExe() {
        if (getParameters().getOsName().get().startsWith("Windows")) {
            return "base/bazel/bazel.cmd";
        } else {
            return "base/bazel/bazel";
        }
    }

    @Override
    public synchronized void close() throws Exception {
        invoked = false;
    }
}
