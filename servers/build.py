#!/usr/bin/env python3
#
# Copyright 2023 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help="License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help="AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
import platform
from log_handler import config_logging
from build_environment import BuildEnvironment
from utils import BAZEL, AOSP_ROOT, run


def main():
    config_logging()

    parser = argparse.ArgumentParser(
        description="Builds the android emulator by invoking bazel. "
        + "The build script will invoke as series of bazel commands "
        + "to construct the emulator distribution. "
        + "It will use bazel and clang from AOSP"
    )
    parser.add_argument(
        "--dist",
        type=str,
        required=True,
        help="The destination directory, artifacts will be placed in this directory.",
    )
    parser.add_argument(
        "--build-id",
        type=str,
        required=True,
        dest="build_id",
        help="The build number used. Presubmit builds should start with the letter P.",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=platform.system(),
        help="The build target, defaults to current os.",
    )

    args = parser.parse_args()

    # Test targets you wish to run,
    list_of_targets = ["@zlib//:all"]

    with BuildEnvironment(args) as cfg:
        command = [BAZEL, "test"] + list_of_targets
        run(command, cfg.get_env(), AOSP_ROOT)


if __name__ == "__main__":
    main()
