#!/bin/bash
# Expected arguments:
# $1 = out_dir
# $2 = dist_dir
# $3 = build_number

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
  echo "Usage: $0 <out_dir> <dest_dir> <build_number>" > /dev/stderr
  exit 1
}

while [[ -n "$1" ]]; do
  if [[ -z "$OUT_DIR" ]]; then
    OUT_DIR="$1"
  elif [[ -z "$DIST_DIR" ]]; then
    DIST_DIR="$1"
  elif [[ -z "$BNUM" ]]; then
    BNUM="$1"
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

GRADLEW="${REPO_ROOT}/tools/gradlew -p ${REPO_ROOT}/tools --no-daemon --info --max-workers=1"

function gradle {
    (set -x ; JAVA_HOME="$JAVA_HOME" OUT_DIR="$OUT_DIR" BUILD_NUMBER="$BNUM" ${GRADLEW} $1 ) || exit $?
}

gradle :base:build-system:integration-test:application:performanceTestJar
mv ${OUT_DIR}/build/base/build-system/integration-test/application/build/libs/performance-test.jar ${DIST_DIR}/performance-test-${BNUM}.jar
