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
from typing import Iterable
import build_environment
import bazel
import time
from pathlib import Path

from log_handler import config_logging


def copy_all(srcs: Iterable[Path], dest: Path):
    for f in srcs:
        f_dest = shutil.copy2(f, dest)
        logging.info("Copied '%s' to '%s'", f, f_dest)


def build_trusty(args):
    zip_name = f"sdk-repo-{args.target}-qemu-{args.build_id}.zip"

    bld_dir = Path("out")
    bld_dir.mkdir(exist_ok=True, parents=True)

    with build_environment.BuildEnvironment(args) as env:
        toolchain = env.repo_root / "external" / "qemu" / "google" / "toolchain"
        build = toolchain / "build-qemu-trusty"
        command = [
            build,
            bld_dir,
            env.dist_dir / zip_name,
        ]
        env.run(command, timeout=1200)


def build_aemu(args):
    bazel_build_targets = ["//hardware/generic/goldfish/emulator:release"]
    bazel_test_targets = ["//hardware/generic/goldfish/emulator:emulator_unit_tests"]

    with build_environment.BuildEnvironment(args) as env:
        logs_dir = env.dist_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        build_id = 'snapshot' if env.is_presubmit else env.build_id
        bzl = bazel.BazelCmd(env)

        bzl_debug = bzl.with_build_flags(
            [
                "--config=ci",
                "--config=debug",
                f"--//hardware/generic/goldfish/emulator:build_id={build_id}",
            ]
        )
        targets = bazel_build_targets[:]
        # Skip tests on windows, we still need to figure out some build issues.
        if not env.is_windows:
            targets += bazel_test_targets
        bzl_debug.test(
            targets,
            invocation_flags=[
                "--test_output=errors",
                "--test_summary=detailed",
                "--verbose_failures",
                "--verbose_explanations",
                f"--explain={logs_dir / 'bazel_test_debug_explain.log'}",
                "--build_metadata=test_definition_name=android_emulator/test_debug",
            ],
            allow_analysis_cache_discard=True,
            allow_no_test=env.is_windows,
        )
        artifacts = bzl_debug.query_artifacts(bazel_build_targets)
        copy_all(artifacts, env.dist_dir)

        bzl_release = bzl.with_build_flags(
            [
                "--config=ci",
                "--config=release",
                f"--//hardware/generic/goldfish/emulator:build_id={build_id}",
            ]
        )
        bzl_release.build(
            bazel_build_targets,
            invocation_flags=[
                "--verbose_failures",
                "--verbose_explanations",
                f"--explain={logs_dir / 'bazel_release_explain.log'}",
                "--build_metadata=test_definition_name=android_emulator/release",
            ],
            allow_analysis_cache_discard=True,
        )
        artifacts = bzl_release.query_artifacts(bazel_build_targets)
        copy_all(artifacts, env.dist_dir)


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
        default="dev",
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
