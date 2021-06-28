#!/bin/bash

readonly script_dir="$(dirname $0)"
readonly dist_dir="$1"
readonly workspace_dir="$(realpath "${script_dir}/../../../")"

export JAVA_HOME="$(realpath "${script_dir}"/../../../prebuilts/studio/jdk/jdk11/linux)"

# Add --warning-mode=fail manually until https://youtrack.jetbrains.com/issue/IDEA-272594 is fixed
(cd "${workspace_dir}"/tools && \
./gradlew --init-script buildSrc/base/release.gradle :publishLocal compileTestJava --configuration-cache --warning-mode=fail && \
# Dokka is incompatible with configuration cache
./gradlew --init-script buildSrc/base/release.gradle :base:gradle-api:dokkaZip --no-configuration-cache) || exit $?
cp "${workspace_dir}/out/dist/documentation.zip" "${dist_dir}"
(cd "${workspace_dir}/out/repo" && zip -r --suffixes sha1:md5:xml "${dist_dir}/gmaven_repo.zip" .) || exit $?
