#!/usr/bin/env python3
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import sys
from pathlib import Path
import bazel
import build_environment
import argparse
import logging

BAZEL_EXTENSIONS = (
    # standard Bazel files
    ".bzl",
    ".bazel",
    # Starlark configuration language:
    # https://github.com/bazelbuild/bazel/commit/a0cd355347b57b17f28695a84af168f9fd200ba1
    ".scl",
    ".sky",
    # WORKSPACE.bzlmod
    ".bzlmod",
    # These aren't standard Bazel files, but some projects use these extensions
    # for Bazel files that are not expected to be read by Bazel in that
    # path, but symlinked elsewhere (e.g. build/bazel/bazel.WORKSPACE, toplevel.WORKSPACE)
    ".BUILD",
    ".WORKSPACE",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="""
        Validates and fixes Bazel files using buildifier.

        This script checks a list of Bazel files for linting errors
        and automatically fixes them if possible. It uses the
        'buildifier' tool, which is part of the Bazel build system.
        Files that are not recognized as Bazel files (e.g., based on
        filename or extension) are ignored.  If any linting errors
        are present, the script will exit with a non-zero status code.
        """,
        epilog="""Examples:

        Check and fix a single BUILD file:
            ./buildifier.py ~/src/emu-dev/hardware/generic/goldfish/emulator/BUILD.bazel

        Check and fix multiple files:
            ./buildifier.py file1.bzl file2.BUILD WORKSPACE test.cpp

        The script exits with a non-zero code if linting errors are found.
        """,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "files", nargs="+", help="List of Bazel files to check and fix."
    )

    args = parser.parse_args()

    with build_environment.create_bazel_environment(args) as env:
        bzl = bazel.BazelCmd(env, capture_output=True).with_build_flags(
            [
                "--noshow_loading_progress",
                "--noshow_progress",
                "--ui_event_filters=,+error,+fail",
                "--show_result=0",
                "--logging=0",
            ]
        )
        # Get the list of files from the command line arguments
        abs_files = [Path(x).absolute() for x in args.files]
        files = [str(x) for x in abs_files if is_bazel_file(x)]
        if not files:
            print("No Bazel files found, ignoring")
            return 0

        try:
            bzl.run(
                target="@buildifier_prebuilt//:buildifier",
                invocation_flags=[
                    "--verbose_failures",
                ],
                allow_analysis_cache_discard=True,
                params=[
                    "-mode=check",
                    "-lint=warn",
                ]
                + files,
            )

        except build_environment.CommandFailedException as cfe:
            logging.warning(cfe.result.stderr)
            bzl.run(
                target="@buildifier_prebuilt//:buildifier",
                params=[
                    "-mode=fix",
                    "-lint=fix",
                ]
                + files,
            )
            logging.error("Buildifier fixed Bazel files. Please amend your commit.")
            sys.exit(cfe.result.returncode)


def is_bazel_file(path: Path) -> bool:
    """Checks if the given file path corresponds to a Bazel file.

    These are basically the set of files that gerrit will consider when
    running lint checks.

    Args:
        file_path: The path to the file.

    Returns:
        True if the file matches Bazel file naming patterns, False otherwise.
    """
    basename = path.name
    if not (basename in ("BUILD", "WORKSPACE") or path.suffix in BAZEL_EXTENSIONS):
        return False
    return path.exists()


if __name__ == "__main__":
    main()
