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
import datetime
import logging
import platform
import shutil
import sys
import time
from pathlib import Path

from build_environment import BuildEnvironment
from log_handler import config_logging
from utils import AOSP_ROOT, BAZEL, run

def copy_zip_files(src_dir: Path, dst_dir: Path):
    """Copies all ZIP files from the source directory and its subdirectories to the destination directory."""

    # Find all ZIP files in the source directory and its subdirectories
    zip_files = src_dir.rglob("*.zip")

    for zip_file in zip_files:
        shutil.copy2(zip_file, dst_dir)  # Use shutil for the actual copy
        logging.info("Copied '%s' to '%s'", zip_file, dst_dir)



def build_trusty(args):
    toolchain = AOSP_ROOT / "external" / "qemu" / "google" / "toolchain"
    build = toolchain / "build-qemu-trusty"
    zip_name = f"sdk-repo-{args.target}-qemu-{args.build_id}.zip"

    dist = Path(args.dist)
    dist.mkdir(exist_ok=True, parents=True)
    bld_dir = Path("out")
    bld_dir.mkdir(exist_ok=True, parents=True)

    with BuildEnvironment(args) as cfg:
        command = [
            build,
            bld_dir,
            dist / zip_name,
        ]
        run(command, cfg.get_env(), AOSP_ROOT)


def build_aemu(args):
    targets = [
        "//hardware/generic/goldfish/emulator:release"
    ]
    system = f"{platform.system().lower()}-x86_64"
    bazel = AOSP_ROOT / "prebuilts" / "bazel" / system / "bazel"
    bazel_explain_file = Path(args.dist) / "logs" / "bazel_explain.log"
    bazel_explain_file.parent.mkdir(parents=True, exist_ok=True)
    with BuildEnvironment(args) as cfg:
        command = [
            bazel,
            "build",
            "--verbose_failures",
            f"--explain={bazel_explain_file}",
            "--verbose_explanations",
            f"--//hardware/generic/goldfish/emulator:build_id={args.build_id}"
        ] + targets
        run(command, cfg.get_env(), AOSP_ROOT)

        # Finally binplace the generated zip.
        res = AOSP_ROOT / "bazel-bin" / "hardware" / "generic" / "goldfish" / "emulator"
        copy_zip_files(res, Path(args.dist))


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
    if "trusty" in args.target:
        build_trusty(args)
    else:
        build_aemu(args)


if __name__ == "__main__":
    start_time = time.monotonic()
    try:
        main()
    except Exception as e:
        logging.fatal("Build failed due to %s", e)
        sys.exit(1)
    finally:
        end_time = time.monotonic()
        logging.info(
            "Completed in: %s", datetime.timedelta(seconds=end_time - start_time)
        )
