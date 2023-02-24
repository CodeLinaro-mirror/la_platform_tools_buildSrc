#!/usr/bin/env python3
#
# Copyright 2018 - The Android Open Source Project
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
from __future__ import absolute_import, division, print_function

import argparse
import logging
import multiprocessing
import os
import platform
import sys

from server_config import ServerConfig

from utils import (
    PYTHON_EXE,
    AOSP_ROOT,
    is_presubmit,
    run,
    log_system_info,
    config_logging,
)


def main(argv):
    config_logging()
    log_system_info()

    # We don't want to be too aggressive with concurrency.
    test_cpu_count = int(multiprocessing.cpu_count() / 4)

    # The build bots tend to be overloaded, so we want to restrict
    # cpu usage to prevent strange timeout issues we have seen in the past.
    # We can increment this once we are building on our own controlled macs
    if platform.system() == "Darwin":
        test_cpu_count = 2

    parser = argparse.ArgumentParser(
        description="Configures the android emulator cmake project so it can be build"
    )
    parser.add_argument(
        "--out_dir", type=str, required=True, help="The output directory"
    )
    parser.add_argument(
        "--dist_dir", type=str, required=True, help="The destination directory"
    )
    parser.add_argument(
        "--build-id",
        type=str,
        default=[],
        required=True,
        dest="build_id",
        help="The emulator build number",
    )
    parser.add_argument(
        "--test_jobs",
        type=int,
        default=test_cpu_count,
        dest="test_jobs",
        help="Specifies  the number of tests to run simultaneously",
    )
    parser.add_argument(
        "--target",
        type=str,
        default=platform.system(),
        help="The build target, defaults to current os",
    )
    parser.add_argument(
        "--qtwebengine",
        action="store_true",
        help="Build emulator with QtWebEngine libraries",
    )
    parser.add_argument(
        "--gfxstream",
        action="store_true",
        help="Build the emulator with the gfxstream libraries",
    )
    parser.add_argument(
        "--gfxstream_only", action="store_true", help="Build gfxstream libraries only"
    )
    parser.add_argument("--crosvm", action="store_true", help="Build crosvm")
    parser.add_argument(
        "--generate", action="store_true", help="Generate and replaceqemu files only."
    )
    parser.add_argument(
        "--with_debug", action="store_true", help="Build debug instead of release"
    )

    args = parser.parse_args()

    os.environ["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"

    target = platform.system().lower()

    if args.target:
        target = args.target.lower()

    crosscompile = target != platform.system().lower()

    if not os.path.isabs(args.out_dir):
        args.out_dir = os.path.join(AOSP_ROOT, args.out_dir)

    # This how we are going to launch the python build script
    launcher = [
        PYTHON_EXE,
        os.path.join(
            AOSP_ROOT, "external", "qemu", "android", "build", "python", "cmake.py"
        ),
    ]

    gfxstream_arg = "--gfxstream"
    crosvm_arg = "--crosvm"

    cmd = [
        "--out",
        args.out_dir,
        "--sdk_build_number",
        args.build_id,
        "--target",
        target,
        "--dist",
        args.dist_dir,
        "--test_jobs",
        str(args.test_jobs),
    ]

    prod = ["--crash", "prod"]

    # Standard arguments for both debug & production.
    if args.qtwebengine:
        cmd.append("--qtwebengine")
    if args.gfxstream_only:
        cmd.append("--gfxstream_only")
    if args.gfxstream:
        cmd.append(gfxstream_arg)
    if args.crosvm:
        cmd.append(crosvm_arg)
    if args.with_debug:
        prod = ["--config", "debug"]

    # Make sure the dist directory exists.
    os.makedirs(args.dist_dir, exist_ok=True)

    # Kick of builds for 2 targets. (debug/release)
    presubmit = is_presubmit(args.build_id)
    with ServerConfig(presubmit, args) as cfg:
        if not target == 'windows':
            # sccache does not (yet?) make life better on windows in gce.
            cmd = cmd + ["--ccache", cfg.sccache]

        run(launcher + cmd + prod, cfg.get_env(), "bld")

        # Let's run the e2e tests.
        if (
            False and # We disable the IntegrationTests due to stability issues.
            presubmit
            and target == "linux"
            and not args.gfxstream
            and not args.gfxstream_only
        ):
            run(launcher + cmd + prod + ["--task", "IntegrationTest"], cfg.get_env(), "tst")

    logging.info("Build completed!")


if __name__ == "__main__":
    try:
        main(sys.argv)
        sys.exit(0)
    except (Exception, KeyboardInterrupt) as exc:
        sys.exit(exc)
