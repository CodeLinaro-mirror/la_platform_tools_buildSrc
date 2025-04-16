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
import bazel
import build_environment
import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="""
        Checks and fixes the bazel lock file "MODULE.bazel.lock".

        This script checks if the lock file needs updating, and updates it if necessary.
        """,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    args = parser.parse_args()

    with build_environment.create_bazel_environment(args) as env:
        bzl = bazel.BazelCmd(env, capture_output=True).with_build_flags(
            [
                "--noshow_loading_progress",
                "--noshow_progress",
                "--ui_event_filters=,+error,+fail",
                "--logging=0",
            ]
        )
        try:
            bzl.mod(["deps", "--lockfile_mode=error"])
        except build_environment.CommandFailedException as cfe:
            logging.warning(cfe.result.stderr)
            bzl.mod(["deps", "--lockfile_mode=update"])
            logging.error("Bazel lockfile updated. Please amend your commit.")
            sys.exit(cfe.result.returncode)


if __name__ == "__main__":
    main()
