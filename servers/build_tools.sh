#!/bin/bash
# Expected arguments:
# $1 = out_dir
# $2 = dist_dir
# $3 = build_number
# $4 = number of --parallel-thread (optional)

set -e

# Normalize to absolute paths.
# Current working directory plus the directory name of the command used to run this script give the
# directory containing this script. Three levels up is the workspace root.
# Use pushd to the directory to get an absolute path.
pushd $(dirname "$0")/../../../
REPO_ROOT=$(pwd)
popd

CURRENT_OS=$(uname | tr A-Z a-z)

function die() {
  echo "$*" > /dev/stderr
  echo "Usage: $0 <out_dir> <dest_dir> <build_number> [num_threads=47]" > /dev/stderr
  exit 1
}

while [[ -n "$1" ]]; do
  if [[ -z "$OUT_DIR" ]]; then
    OUT_DIR="$1"
  elif [[ -z "$DIST_DIR" ]]; then
    DIST_DIR="$1"
  elif [[ -z "$BNUM" ]]; then
    BNUM="$1"
  elif [[ -z "$NUM_THREADS" ]]; then
    NUM_THREADS="$1"
  else
    die "[$0] Unknown parameter: $1"
  fi
  shift
done

if [[ -z "$OUT_DIR"  ]]; then die "## Error: Missing out folder"; fi
if [[ -z "$DIST_DIR" ]]; then die "## Error: Missing destination folder"; fi
if [[ -z "$BNUM"     ]]; then die "## Error: Missing build number"; fi

if [[ "$OUT_DIR" != /* ]]
then
    OUT_DIR="$REPO_ROOT/$OUT_DIR"
fi

# Use the studio prebuilt JDK rather than the ambient one.
# This means that local developers and build servers use the same JDK, avoiding incidents of
# continuous builds being broken due to the ambient java not matching the prebuilt one.
# Similar to tools/idea/build_studio.sh
case `uname -s` in
    MSYS_NT-10.0)
        PREBUILT_JDK_RELATIVE_PATH=win64
        ;;
    MINGW64_NT-10.0)
        PREBUILT_JDK_RELATIVE_PATH=win64
        ;;
    CYGWIN_NT-10.0)
        PREBUILT_JDK_RELATIVE_PATH=win64
        ;;
    Darwin)
        PREBUILT_JDK_RELATIVE_PATH=mac/Contents/Home
        ;;
    *)
        PREBUILT_JDK_RELATIVE_PATH=linux
        ;;
esac

JAVA_HOME="$REPO_ROOT"/prebuilts/studio/jdk/${PREBUILT_JDK_RELATIVE_PATH}
BAZEL="${REPO_ROOT}/tools/base/bazel/bazel --output_base $OUT_DIR"

function bazel {
    (set -x ; JAVA_HOME="$JAVA_HOME" BUILD_NUMBER="$BNUM" ${BAZEL} $@) || exit $?
}

mkdir -p ${DIST_DIR}

bazel test //tools/base/sdklib:commandlinetoolstest
BAZEL_BIN=`${BAZEL} info bazel-bin`
cp ${BAZEL_BIN}/tools/base/sdklib/commandlinetools_*.zip ${DIST_DIR}
