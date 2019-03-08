#!/bin/bash

readonly script_dir="$(dirname $0)"
readonly dist_dir="$1"

export JAVA_HOME="$(realpath "${script_dir}"/../../../prebuilts/studio/jdk/linux)"
export DIST_DIR=$dist_dir

(cd "${script_dir}"/../.. && ./gradlew --init-script buildSrc/base/release.gradle "-PlocalRepo=${dist_dir}/gmaven_repo" :publishLocal :zipOfflineRepo) || exit $?
(cd "${dist_dir}/gmaven_repo" && zip -r --suffixes sha1:md5:xml "${dist_dir}/gmaven_repo.zip" .) || exit $?
