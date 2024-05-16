/*
 * Copyright (C) 2024 The Android Open Source Project
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

import com.android.tools.internal.AgpVersion;
import com.android.tools.internal.TaskUtils;
import org.apache.commons.io.IOUtils;
import org.gradle.api.DefaultTask;
import org.gradle.api.file.ConfigurableFileCollection;
import org.gradle.api.file.DirectoryProperty;
import org.gradle.api.provider.Property;
import org.gradle.api.tasks.Input;
import org.gradle.api.tasks.InputDirectory;
import org.gradle.api.tasks.InputFiles;
import org.gradle.api.tasks.OutputDirectory;
import org.gradle.api.tasks.PathSensitive;
import org.gradle.api.tasks.PathSensitivity;
import org.gradle.api.tasks.TaskAction;
import org.jetbrains.annotations.NotNull;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;

public abstract class GenerateApiReleaseNotes extends DefaultTask {
    @InputDirectory
    public abstract DirectoryProperty getInputDirectory();

    @OutputDirectory
    public abstract DirectoryProperty getOutputDirectory();

    @Input
    @org.gradle.api.tasks.Optional
    public abstract Property<String> getCurrentVersion();

    @Input
    @org.gradle.api.tasks.Optional
    public abstract Property<String> getPreviousVersion();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract ConfigurableFileCollection getOldApiFiles();

    public GenerateApiReleaseNotes() {
        getCurrentVersion().convention("current.txt");
    }

    @TaskAction
    public void generate() throws IOException {
        File olderApiSignatureFile = null;
        if (!getPreviousVersion().isPresent()) {
            Optional<AgpVersion> previousStableVersion = getOldApiFiles().getFiles().stream()
                    .map(file -> AgpVersion.parseFileNameOrNull(file.getName()))
                    .filter(Objects::nonNull).max(Comparator.naturalOrder());
            assert previousStableVersion.isPresent();
            olderApiSignatureFile = getProject().getLayout().getProjectDirectory().dir("previous-gradle-apis").file(previousStableVersion.get() + ".txt").getAsFile();
        } else {
            olderApiSignatureFile = getProject().getLayout().getProjectDirectory().dir("previous-gradle-apis").file(getPreviousVersion().get()).getAsFile();
        }

        File currentApiSignatureFile = getInputDirectory().get().file(getCurrentVersion().get()).getAsFile();

        if (!currentApiSignatureFile.exists() || !olderApiSignatureFile.exists()) return;
        generateReleaseNotes(currentApiSignatureFile, olderApiSignatureFile);
    }

    private void generateReleaseNotes(File currentApiSignatureFile, File olderApiSignatureFile) throws IOException {
        Map<String, ClassDefinition> currentApiElements =
                parseApiSignature(IOUtils.toString(new FileReader(currentApiSignatureFile)))
                        .stream()
                        .collect(Collectors.toMap(
                                clazz -> clazz.getPackageName() + "." + clazz.getClassName(), clazz -> clazz));

        Map<String, ClassDefinition> olderApiElements =
                parseApiSignature(IOUtils.toString(new FileReader(olderApiSignatureFile)))
                        .stream()
                        .collect(Collectors.toMap(
                                clazz -> clazz.getPackageName() + "." + clazz.getClassName(), clazz -> clazz));

        findStabilizedApis(currentApiElements, olderApiElements);

    }

    // Finds apis that were incubating in older api version but are not in current
    private void findStabilizedApis(
            Map<String, ClassDefinition> currentApiElements,
            Map<String, ClassDefinition> olderApiElements
    ) {
        StringBuilder sb = new StringBuilder();
        sb.append("Android Gradle plugin API updates").append("\n");

        for (String className : olderApiElements.keySet()) {
            com.android.tools.internal.metalava.ClassDefinition oldClassDefinition = olderApiElements.get(className);
            ClassDefinition classDefinition = currentApiElements.get(className);
            if (classDefinition != null && oldClassDefinition.isIncubating() && !classDefinition.isIncubating()) {
                // class has become stable
                sb.append(className).append(" is now stable\n");
            }
        }

        writeToFile(sb, "stable-apis.txt");
    }

    private void writeToFile(StringBuilder input, String fileName) {
        String filePath = TaskUtils.asArg(getOutputDirectory()) + "/" + fileName;
        try (FileWriter fileWriter = new FileWriter(filePath)) {
            fileWriter.write(input.toString());
        } catch (IOException e) {
            throw new RuntimeException(e);
        } finally {
            System.out.println(filePath + " written");
        }
    }

    private List<ClassDefinition> parseApiSignature(String inputString) {
        List<ClassDefinition> classes = new ArrayList<>();
        String currentPackage = "";
        try (BufferedReader br = new BufferedReader(new StringReader(inputString))) {
            String line;
            while ((line = br.readLine()) != null) {
                line = line.trim();

                if (line.startsWith("package")) {
                    currentPackage = line.substring(line.indexOf(" ") + 1, line.indexOf(" {"));
                } else if (line.contains("public class") || line.contains("public interface") ||
                        line.contains("public enum") || line.contains("public final class") ||
                        line.contains("public abstract class") || line.contains("public static final class")) {

                    boolean isDeprecated = line.startsWith("@Deprecated");
                    line = line.replace("@Deprecated", "").trim();
                    boolean isIncubating = line.startsWith("@org.gradle.api.Incubating");
                    line = line.replace("@org.gradle.api.Incubating", "").trim();

                    String className = getClassName(line);
                    classes.add(new ClassDefinition(currentPackage, className, isIncubating));
                } else if (line.startsWith("method")) {
                    String methodName = extractMethodName(line);
                    boolean isIncubating = line.contains("@org.gradle.api.Incubating");
                    classes.get(classes.size() - 1).getMethods().add(
                            new MethodOrFieldDefinition(methodName, isIncubating)
                    );
                } else if (line.startsWith("field") || line.startsWith("property") || line.startsWith("enum_constant")) {
                    String fieldName = line.substring(line.lastIndexOf(" ") + 1).replace(";", "").replaceAll("\\(.*?\\)", "");
                    boolean isIncubating = line.contains("@org.gradle.api.Incubating");
                    classes.get(classes.size() - 1).getFields().add(
                            new MethodOrFieldDefinition(fieldName, isIncubating)
                    );
                }
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
        return classes;
    }

    @NotNull
    private static String getClassName(String line) {
        String className = "";
        if (line.startsWith("public class")) {
            className = line.split("\\s+")[2];
        } else if (line.startsWith("public interface")) {
            className = line.split("\\s+")[2];
        } else if (line.startsWith("public enum")) {
            className = line.split("\\s+")[2];
        } else if (line.startsWith("public final class")) {
            className = line.split("\\s+")[3];
        } else if (line.startsWith("public abstract class")) {
            className = line.split("\\s+")[3];
        } else if (line.startsWith("public static interface")) {
            className = line.split("\\s+")[3];
        } else if (line.startsWith("public static final class")) {
            className = line.split("\\s+")[4];
        } else if (line.startsWith("public abstract static class")) {
            className = line.split("\\s+")[4];
        } else if (line.startsWith("public abstract sealed class")) {
            className = line.split("\\s+")[4];
        }
        if (className.contains("<")) {
            className = className.substring(0, className.indexOf('<'));
        }
        if (className.isEmpty()) {
            throw new RuntimeException("Class name can not be empty. Inspect the input (" + line + ") and adjust the code.");
        }
        return className;
    }

    private String extractMethodName(String input) {
        int lastOpenParenIndex = input.lastIndexOf('(');

        if (lastOpenParenIndex != -1) {
            int lastSpaceBeforeParenIndex = input.lastIndexOf(' ', lastOpenParenIndex - 1);
            if (lastSpaceBeforeParenIndex != -1) {
                return input.substring(lastSpaceBeforeParenIndex + 1, lastOpenParenIndex).trim();
            }
        }
        return input;
    }
}

class ClassDefinition {
    private final String packageName;
    private final String className;
    private final List<MethodOrFieldDefinition> methods;
    private final List<MethodOrFieldDefinition> fields;
    private final boolean isIncubating;

    public ClassDefinition(String packageName, String className, boolean isIncubating) {
        this.packageName = packageName;
        this.className = className;
        this.methods = new ArrayList<>();
        this.fields = new ArrayList<>();
        this.isIncubating = isIncubating;
    }

    public String getPackageName() {
        return packageName;
    }

    public String getClassName() {
        return className;
    }

    public List<MethodOrFieldDefinition> getMethods() {
        return methods;
    }

    public List<MethodOrFieldDefinition> getFields() {
        return fields;
    }

    public boolean isIncubating() {
        return isIncubating;
    }
}

class MethodOrFieldDefinition {
    private final String name;
    private final boolean isIncubating;

    public MethodOrFieldDefinition(String name, boolean isIncubating) {
        this.name = name;
        this.isIncubating = isIncubating;
    }

    public String getName() {
        return name;
    }

    public boolean isIncubating() {
        return isIncubating;
    }
}
