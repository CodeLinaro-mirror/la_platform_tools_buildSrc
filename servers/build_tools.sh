#!/bin/bash

if [[ -z "${DIST_DIR}" ]]; then
  echo "DIST_DIR missing from environment" >&2
  exit 1
fi

readonly BAZEL="$(dirname "$0")/../../../tools/base/bazel/bazel"

"${BAZEL}" test //tools/base/sdklib:commandlinetoolstest || exit $?
BAZEL_BIN="$("${BAZEL}" info bazel-bin)"
cp -av "${BAZEL_BIN}"/tools/base/sdklib/commandlinetools_*.zip "${DIST_DIR}"
