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

package com.android.tools.internal.metalava;

import com.google.common.collect.ImmutableList;
import org.gradle.api.provider.ListProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.provider.SetProperty;
import org.gradle.process.ExecOperations;
import org.gradle.workers.WorkAction;
import org.gradle.workers.WorkParameters;

import javax.inject.Inject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public abstract class MetalavaWorkAction implements WorkAction<MetalavaWorkAction.MetalavaParams> {

    private static final String METALAVA_MAIN = "com.android.tools.metalava.Driver";


    interface MetalavaParams extends WorkParameters {
        ListProperty<String> getArgs();
        SetProperty<File> getMetalavaClasspath();
        Property<Boolean> getK2UastEnabled();
    }

    @Inject
    public abstract ExecOperations getExecOperations();

    @Override
    public void execute() {
        ByteArrayOutputStream outputStream = new ByteArrayOutputStream();
        List<String> args = new ArrayList<>(getParameters().getArgs().get());
        if (getParameters().getK2UastEnabled().get()) {
            args.add("--Xuse-k2-uast");
        }
        try {
            getExecOperations().javaexec(spec -> {
                // Intellij core reflects into java.util.ResourceBundle
                spec.setJvmArgs(ImmutableList.of(
                        "--add-opens",
                        "java.base/java.util=ALL-UNNAMED"
                ));
                spec.classpath(getParameters().getMetalavaClasspath().get());
                spec.getMainClass().set(METALAVA_MAIN);
                spec.args(args);
                spec.setStandardOutput(outputStream);
                spec.setErrorOutput(outputStream);
            });
        } catch (Exception e) {
            System.err.println(outputStream.toString(StandardCharsets.UTF_8));
            throw new RuntimeException("Failed to run metalava.", e);
        }
    }
}
