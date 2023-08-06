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

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonPrimitive;
import com.google.gson.JsonSerializationContext;
import com.google.gson.JsonSerializer;
import java.io.File;
import java.io.IOException;
import java.lang.reflect.Type;
import org.gradle.api.file.FileCollection;

public class GsonUtils {
    public static Gson createGson() {
        return new GsonBuilder()
            .setPrettyPrinting()
            .registerTypeAdapter(File.class, new CanonicalFileSerializer())
        .registerTypeAdapter(FileCollection.class, new FileCollectionSerializer())
        .create();
    }

    /** Serializer for Gradle's [FileCollection] */
    private static class FileCollectionSerializer implements JsonSerializer<FileCollection> {
        @Override
        public JsonElement serialize(FileCollection src, Type type, JsonSerializationContext context) {
            return context.serialize(src.getFiles());
        }
    }

    /**
     * Serializer for [File] instances in the Dokka CLI model.
     * Dokka doesn't work well with relative paths hence we use a canonical paths while setting up
     * its parameters.
     */
    private static class CanonicalFileSerializer implements JsonSerializer<File> {
        @Override
        public JsonElement serialize(File file, Type type, JsonSerializationContext jsonSerializationContext) {
            try {
                return new JsonPrimitive(file.getCanonicalPath());
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        }
    }
}