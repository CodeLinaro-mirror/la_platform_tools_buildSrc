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

import com.google.common.io.ByteStreams;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.jar.JarEntry;
import java.util.jar.JarOutputStream;

public class OfflineRepoUtils {
    private static final String SUBDIR_PREFIX = "android-gradle-plugin-";
    private static final String README_FILE_NAME = "README";

    public static String getSubDirName(String version) {
        return SUBDIR_PREFIX + version;
    }

    public static void extractReadme(Path outZip, String version) throws IOException {
        try (InputStream readmeContent =
                        new BufferedInputStream(
                                OfflineRepoUtils.class.getResourceAsStream("README.md"));
             JarOutputStream jarOutputStream =
                        new JarOutputStream(
                                new BufferedOutputStream(Files.newOutputStream(outZip)))) {
            jarOutputStream.putNextEntry(
                    new JarEntry(getSubDirName(version) + "/" + README_FILE_NAME));
            ByteStreams.copy(readmeContent, jarOutputStream);
        }
    }
}
