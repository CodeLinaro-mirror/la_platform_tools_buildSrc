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
import org.gradle.api.tasks.options.Option;
import org.jetbrains.annotations.NotNull;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.Optional;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

public abstract class GenerateApiReleaseNotes extends DefaultTask {
    @InputDirectory
    public abstract DirectoryProperty getInputDirectory();

    @OutputDirectory
    public abstract DirectoryProperty getOutputDirectory();

    @Input
    public abstract Property<String> getAgpBuildVersion();

    @Input
    @org.gradle.api.tasks.Optional
    @Option(option = "current-version", description = "The current AGP version")
    public abstract Property<String> getCurrentVersion();

    @Input
    @org.gradle.api.tasks.Optional
    @Option(option = "previous-version", description = "The previous AGP version to compare against")
    public abstract Property<String> getPreviousVersion();

    @InputFiles
    @PathSensitive(PathSensitivity.RELATIVE)
    public abstract ConfigurableFileCollection getOldApiFiles();

    public GenerateApiReleaseNotes() { }

    @TaskAction
    public void generate() throws IOException {
        File currentApiSignatureFile = null;
        File olderApiSignatureFile = null;
        String previousVersion = null;
        String currentVersion = null;
        
        if (getPreviousVersion().isPresent()) {
            olderApiSignatureFile = getProject().getLayout().getProjectDirectory()
                    .dir("previous-gradle-apis").file(getPreviousVersion().get() + ".txt").getAsFile();
            if (!olderApiSignatureFile.exists()) {
                throw new RuntimeException(
                        "Supplied previous-version (" + getPreviousVersion().get() + ") is not present in previous-gradle-apis folder."
                );
            }
            previousVersion = getPreviousVersion().get();
        } else {
            Optional<AgpVersion> previousStableVersion = getOldApiFiles().getFiles().stream()
                    .map(file -> AgpVersion.parseFileNameOrNull(file.getName()))
                    .filter(Objects::nonNull).max(Comparator.naturalOrder());
            if (previousStableVersion.isEmpty()) {
                throw new RuntimeException("I tried to find the latest API signature file in previous-gradle-apis folder" +
                        "but could not find one. Please run ./gradlew generateOldApis to generate them and then generate release notes");
            }
            olderApiSignatureFile = getProject().getLayout().getProjectDirectory()
                    .dir("previous-gradle-apis").file(previousStableVersion.get() + ".txt").getAsFile();
            previousVersion = previousStableVersion.get().toString();
        }

        if (getCurrentVersion().isPresent()) {
            currentApiSignatureFile = getProject().getLayout().getProjectDirectory()
                    .dir("previous-gradle-apis").file(getCurrentVersion().get() + ".txt").getAsFile();

            if (!currentApiSignatureFile.exists()) {
                throw new RuntimeException(
                        "Supplied current-version (" + getCurrentVersion().get() + ") is not present in previous-gradle-apis folder."
                );
            }
            currentVersion = getCurrentVersion().get();
        } else {
            currentApiSignatureFile = getInputDirectory().get().file("current.txt").getAsFile();
            currentVersion = getAgpBuildVersion().get();
        }

        generateReleaseNotes(currentApiSignatureFile, olderApiSignatureFile, previousVersion, currentVersion);
    }

    private void generateReleaseNotes(
            File currentApiSignatureFile,
            File olderApiSignatureFile,
            String previousVersion,
            String currentVersion
    ) throws IOException {
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
        generateApiChangesReport(currentApiElements, olderApiElements, previousVersion, currentVersion);
    }

    private void generateApiChangesReport(
            Map<String, ClassDefinition> currentApiElements,
            Map<String, ClassDefinition> olderApiElements,
            String previousVersion,
            String currentVersion
    ) {
        StringBuilder report = new StringBuilder();
        report.append("Android Gradle plugin API updates")
                .append(" (between ").append(previousVersion).append(" and ").append(currentVersion).append(")")
                .append("\n\n");

        StringBuilder newApis = new StringBuilder();
        StringBuilder stabilizedApis = new StringBuilder();
        StringBuilder deprecatedApis = new StringBuilder();
        StringBuilder removedApis = new StringBuilder();

        Set<String> allClassNames = new java.util.HashSet<>();
        allClassNames.addAll(currentApiElements.keySet());
        allClassNames.addAll(olderApiElements.keySet());

        for (String className : allClassNames.stream().sorted().toList()) {
            ClassDefinition currentClass = currentApiElements.get(className);
            ClassDefinition oldClass = olderApiElements.get(className);

            if (currentClass != null && oldClass == null) {
                newApis.append("Class ").append(className).append(getStabilitySuffix(currentClass)).append(" has been added.\n");
            } else if (currentClass == null && oldClass != null) {
                removedApis.append("Class ").append(className).append(" has been removed.\n");
            } else if (currentClass != null) {
                // Class-level changes
                if (oldClass.isIncubating() && !currentClass.isIncubating()) {
                    stabilizedApis.append("Class ").append(className).append(" is now stable.\n");
                }
                if (!oldClass.isDeprecated() && currentClass.isDeprecated()) {
                    deprecatedApis.append("Class ").append(className).append(" is now deprecated.\n");
                }

                // Member-level changes
                compareMembers(className, "Method", currentClass.getMethods(), oldClass.getMethods(), newApis, stabilizedApis, deprecatedApis, removedApis);
                compareMembers(className, "Field", currentClass.getFields(), oldClass.getFields(), newApis, stabilizedApis, deprecatedApis, removedApis);
            }
        }

        int changeCount = 0;
        if (!newApis.isEmpty()) {
            report.append("New APIs\n--------\n").append(newApis).append("\n");
            changeCount++;
        }
        if (!stabilizedApis.isEmpty()) {
            report.append("Stabilized APIs\n---------------\n").append(stabilizedApis).append("\n");
            changeCount++;
        }
        if (!deprecatedApis.isEmpty()) {
            report.append("Newly Deprecated APIs\n---------------------\n").append(deprecatedApis).append("\n");
            changeCount++;
        }
        if (!removedApis.isEmpty()) {
            report.append("Removed APIs (Breaking Changes)\n-------------------------------\n").append(removedApis).append("\n");
            changeCount++;
        }

        if (changeCount == 0) {
            report.append("No notable API changes were detected.");
        }

        writeToFile(report, "stable-apis.txt");
    }

    private void compareMembers(
            String className,
            String memberType,
            Set<MethodOrFieldDefinition> currentMembers,
            Set<MethodOrFieldDefinition> oldMembers,
            StringBuilder newApis,
            StringBuilder stabilizedApis,
            StringBuilder deprecatedApis,
            StringBuilder removedApis
    ) {
        try {
            Map<String, MethodOrFieldDefinition> currentMemberMap = currentMembers.stream().collect(Collectors.toMap(MethodOrFieldDefinition::getName, m -> m));
            Map<String, MethodOrFieldDefinition> oldMemberMap = oldMembers.stream().collect(Collectors.toMap(MethodOrFieldDefinition::getName, m -> m));

            Set<String> allMemberNames = new java.util.HashSet<>();
            allMemberNames.addAll(currentMemberMap.keySet());
            allMemberNames.addAll(oldMemberMap.keySet());

            for (String memberName : allMemberNames.stream().sorted().toList()) {
                MethodOrFieldDefinition currentMember = currentMemberMap.get(memberName);
                MethodOrFieldDefinition oldMember = oldMemberMap.get(memberName);

                if (currentMember != null && oldMember == null) {
                    newApis.append(String.format("%s %s in %s%s has been added.\n", memberType, memberName, className, getStabilitySuffix(currentMember)));
                } else if (currentMember == null && oldMember != null) {
                    removedApis.append(String.format("%s %s from %s has been removed.\n", memberType, memberName, className));
                } else if (currentMember != null) {
                    if (oldMember.isIncubating() && !currentMember.isIncubating()) {
                        stabilizedApis.append(String.format("%s %s in %s is now stable.\n", memberType, memberName, className));
                    }
                    if (!oldMember.isDeprecated() && currentMember.isDeprecated()) {
                        deprecatedApis.append(String.format("%s %s in %s is now deprecated.\n", memberType, memberName, className));
                    }
                }
            }
        } catch (IllegalStateException e) {
            System.out.println("Something went wrong with class " + className);
            throw new RuntimeException(e);
        }
    }

    private void writeToFile(StringBuilder input, String fileName) {
        String filePath = TaskUtils.asArg(getOutputDirectory()) + "/" + fileName;
        try (FileWriter fileWriter = new FileWriter(filePath)) {
            fileWriter.write(input.toString());
            getLogger().lifecycle("API change report written to: {}", new File(filePath).getAbsolutePath());
        } catch (IOException e) {
            throw new RuntimeException(e);
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
                    boolean isReplacedByIncubating = line.startsWith("@com.android.build.api.annotations.ReplacedByIncubating");
                    if (isReplacedByIncubating) {
                        Pattern p = Pattern.compile("@com\\.android\\.build\\.api\\.annotations\\.ReplacedByIncubating(\\(.*\\))?");
                        line = p.matcher(line).replaceAll("");
                    }
                    boolean isIncubating = line.startsWith("@org.gradle.api.Incubating");
                    line = line.replace("@org.gradle.api.Incubating", "").trim();

                    String className = getClassName(line);
                    classes.add(new ClassDefinition(currentPackage, className, isIncubating, isDeprecated));
                } else if (line.startsWith("method")) {
                    String methodName = extractMethodName(line);
                    boolean isIncubating = line.contains("@org.gradle.api.Incubating");
                    boolean isDeprecated = line.contains("@java.lang.Deprecated");
                    classes.get(classes.size() - 1).getMethods().add(
                            new MethodOrFieldDefinition(methodName, isIncubating, isDeprecated)
                    );
                } else if (line.startsWith("field") || line.startsWith("property") || line.startsWith("enum_constant")) {
                    String fieldName = line.substring(line.lastIndexOf(" ") + 1).replace(";", "").replaceAll("\\(.*?\\)", "");
                    boolean isIncubating = line.contains("@org.gradle.api.Incubating");
                    boolean isDeprecated = line.contains("@java.lang.Deprecated");
                    classes.get(classes.size() - 1).getFields().add(
                            new MethodOrFieldDefinition(fieldName, isIncubating, isDeprecated)
                    );
                }
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
        return classes;
    }

    private String getStabilitySuffix(ClassDefinition clazz) {
        if (clazz.isIncubating()) return " (incubating)";
        if (clazz.isDeprecated()) return " (deprecated)";
        return "";
    }

    private String getStabilitySuffix(MethodOrFieldDefinition member) {
        if (member.isIncubating()) return " (incubating)";
        if (member.isDeprecated()) return " (deprecated)";
        return "";
    }

    @NotNull
    private static String getClassName(String line) {
        String[] parts = line.split("\\s+");
        int typeIndex = -1;
        for (int i = 0; i < parts.length; i++) {
            if (parts[i].equals("class") || parts[i].equals("interface") || parts[i].equals("enum")) {
                typeIndex = i;
                break;
            }
        }

        if (typeIndex != -1 && typeIndex + 1 < parts.length) {
            String className = parts[typeIndex + 1];
            // if it's parameterized, ignore the parameters, e.g. `Foo<Bar>` becomes `Foo`
            if (className.contains("<")) {
                className = className.substring(0, className.indexOf('<'));
            }
            return className;
        }

        throw new RuntimeException("Class name can not be empty. Inspect the input (" + line + ") and adjust the code.");
    }

    private String extractMethodName(String input) {
        // Extracts the full method signature, e.g., "getByName(java.lang.String)" or "getMajor()"
        int openParen = input.lastIndexOf('(');

        if (openParen != -1) {
            // Method with parameters, e.g.
            // method @... public void setApiLevel(int);
            int closeParen = input.lastIndexOf(')');
            if (closeParen == -1 || closeParen < openParen) {
                // This should not happen for a valid signature file.
                // Fallback to something reasonable.
                String trimmed = input.trim();
                return trimmed.substring(trimmed.lastIndexOf(' ') + 1);
            }

            String partBeforeParen = input.substring(0, openParen);
            String name = partBeforeParen.substring(partBeforeParen.lastIndexOf(' ') + 1);
            return name + input.substring(openParen, closeParen + 1);
        } else {
            // Method without parameters, e.g.
            // method public int getMajor();
            String trimmed = input.trim();
            String withoutSemicolon = trimmed.substring(0, trimmed.length() - 1);
            String name = withoutSemicolon.substring(withoutSemicolon.lastIndexOf(' ') + 1);
            return name + "()";
        }
    }
}

class ClassDefinition {
    private final String packageName;
    private final String className;
    private final Set<MethodOrFieldDefinition> methods;
    private final Set<MethodOrFieldDefinition> fields;
    private final boolean isIncubating;
    private final boolean isDeprecated;

    public ClassDefinition(String packageName, String className, boolean isIncubating, boolean isDeprecated) {
        this.packageName = packageName;
        this.className = className;
        this.methods = new HashSet<>();
        this.fields = new HashSet<>();
        this.isIncubating = isIncubating;
        this.isDeprecated = isDeprecated;
    }

    public String getPackageName() {
        return packageName;
    }

    public String getClassName() {
        return className;
    }

    public Set<MethodOrFieldDefinition> getMethods() {
        return methods;
    }

    public Set<MethodOrFieldDefinition> getFields() {
        return fields;
    }

    public boolean isIncubating() {
        return isIncubating;
    }

    public boolean isDeprecated() {
        return isDeprecated;
    }
}

class MethodOrFieldDefinition {
    private final String name;
    private final boolean isIncubating;
    private final boolean isDeprecated;

    public MethodOrFieldDefinition(String name, boolean isIncubating, boolean isDeprecated) {
        this.name = name;
        this.isIncubating = isIncubating;
        this.isDeprecated = isDeprecated;
    }

    public String getName() {
        return name;
    }

    public boolean isDeprecated() {
        return isDeprecated;
    }

    public boolean isIncubating() {
        return isIncubating;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        MethodOrFieldDefinition that = (MethodOrFieldDefinition) o;
        return Objects.equals(name, that.name);
    }

    @Override
    public int hashCode() {
        return Objects.hash(name);
    }
}
