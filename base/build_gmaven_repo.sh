#!/bin/bash

readonly script_dir="$(dirname $0)"
readonly dist_dir="$1"
readonly workspace_dir="$(realpath "${script_dir}/../../../")"

export JAVA_HOME="$(realpath "${script_dir}"/../../../prebuilts/studio/jdk/linux)"

(cd "${workspace_dir}"/tools && ./gradlew --init-script buildSrc/base/release.gradle :publishLocal :zipOfflineRepo) || exit $?
cp "${workspace_dir}/out/dist/offline_repo.zip" "${dist_dir}"
(cd "${workspace_dir}/out/repo" && zip -r --suffixes sha1:md5:xml "${dist_dir}/gmaven_repo.zip" .) || exit $?
