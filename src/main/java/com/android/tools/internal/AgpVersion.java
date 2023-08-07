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

package com.android.tools.internal;

import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class AgpVersion implements Comparable<AgpVersion> {

    private final int major;
    private final int minor;
    private final int patch;
    private final String preview;

    public AgpVersion(int major, int minor, int patch, String preview) {
        this.major = major;
        this.minor = minor;
        this.patch = patch;
        this.preview = preview;
    }

    @Override
    public int compareTo(AgpVersion o) {
        if (major != o.major) {
            return major - o.major;
        } else if (minor != o.minor) {
            return minor - o.minor;
        } else if (patch != o.patch) {
            return patch - o.patch;
        } else if (!Objects.equals(preview, o.preview)) {
            if (preview == null || o.preview == null) {
                return Boolean.compare(preview == null, o.preview == null);
            } else {
                return preview.compareTo(o.preview);
            }
        }
        return 0;
    }

    @Override
    public String toString() {
        if (preview != null) {
            return major + "." + minor + "." + patch + "-" + preview;
        } else {
            return major + "." + minor + "." + patch;
        }
    }

    private static final Pattern VERSION_REGEX = Pattern.compile("^(\\d+)\\.(\\d+)\\.(\\d+)(-.+)?$");

    public static AgpVersion parseOrNull(String versionString) {
        Matcher matcher = VERSION_REGEX.matcher(versionString);
        if (matcher.matches()) {
            return new AgpVersion(
                Integer.parseInt(matcher.group(1)),
                Integer.parseInt(matcher.group(2)),
                Integer.parseInt(matcher.group(3)),
                matcher.group(4) != null ? matcher.group(4).substring(1) : null
            );
        }
        return null;
    }

    private static final Pattern API_FILE_PATTERN = Pattern.compile("^(.*).txt$");

    public static AgpVersion parseFileNameOrNull(String filename) {
        Matcher matcher = API_FILE_PATTERN.matcher(filename);
        if (matcher.matches()) {
            return parseOrNull(matcher.group(1));
        }
        return null;
    }
}