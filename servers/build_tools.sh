#!/bin/bash

readonly BAZEL="$(dirname "$0")/../../../tools/base/bazel/bazel"

"${BAZEL}" test //tools/base/sdklib:commandlinetoolstest || exit $?

if [[ -d "${DIST_DIR}" ]]; then
  readonly BAZEL_BIN="$("${BAZEL}" info bazel-bin)"
  cp -av "${BAZEL_BIN}"/tools/base/sdklib/commandlinetools_*.zip "${DIST_DIR}"
fi
