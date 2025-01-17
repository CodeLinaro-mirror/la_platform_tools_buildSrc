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
import datetime
import logging
import multiprocessing
import os
import platform
import sys
import time

from server_config import ServerConfig
from pyrunner import AospPyRunner

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
        "--prebuilts",
        dest="prebuilts",
        action="store",
        nargs="*",
        help="Builds the specified prebuilts (i.e. --prebuilts qt ffmpeg), or all prebuilts if no argument is provided",
    )
    parser.add_argument(
        "--enable_system_rust",
        action="store_true",
        help="Build the emulator with the System Rust on the host machine",
    )
    parser.add_argument(
        "--change_info",
        help="Path to the change_info.json file, if any.",
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

    # to avoid running into path length 260 on windows
    # use C:\buildbot\src\out instead of C:\buildbot\src\googleplex-android\emu-main-dev\out
    args.out_dir = os.path.normpath(os.path.join(AOSP_ROOT, "..", "..", args.out_dir))

    if not os.path.isabs(args.out_dir):
        args.out_dir = os.path.join(AOSP_ROOT, args.out_dir)

    # This how we are going to launch the python build script

    skip_display_init = True if args.prebuilts is not None else False
    # We can't use AOSP python for prebuilts build because some of the repositories require
    # downloading things from the internet (git submodules, gclient stuff), and SSL is ripped out
    # in AOSP python.
    if args.prebuilts is None:
        repo = os.path.join(
            AOSP_ROOT, "external", "adt-infra", "devpi", "repo", "simple"
        )
        pyrun = AospPyRunner(repo=repo, skip_display_init=skip_display_init)

    def run_python(run_args, env, timeout, log_prefix):
        if args.prebuilts is None:
            pyrun.run(run_args, env, timeout=timeout, log_prefix=log_prefix)
        else:
            run(cmd=[PYTHON_EXE] + run_args, env=env, log_prefix=log_prefix)

    launcher = [
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

    # Standard arguments for both debug & production.
    if args.qtwebengine:
        cmd.append("--qtwebengine")
    if args.gfxstream_only:
        cmd.append("--gfxstream_only")
    if args.gfxstream:
        cmd.append(gfxstream_arg)
    if args.crosvm:
        cmd.append(crosvm_arg)
    if args.enable_system_rust:
        cmd = cmd + ["--feature", "enable_system_rust"]
    if args.with_debug:
        cmd = cmd + ["--config", "debug"]
    if args.prebuilts is not None:
        cmd = cmd + ["--prebuilts"] + args.prebuilts

    # Make sure the dist directory exists.
    os.makedirs(args.dist_dir, exist_ok=True)

    # Kick of builds for 2 targets. (debug/release)
    presubmit = is_presubmit(args.build_id)

    if presubmit:
        logging.info("Not uploading symbols for presubmit builds.")
    else:
        cmd = cmd + ["--crash", "prod"]

    with ServerConfig(presubmit, args) as cfg:
        if not target == "windows":
            # sccache does not (yet?) make life better on windows in gce.
            cmd = cmd + ["--ccache", cfg.sccache]

        run_python(launcher + cmd, cfg.get_env(), timeout=3600, log_prefix="bld")

    logging.info("Build completed!")


if __name__ == "__main__":
    start_time = time.monotonic()
    try:
        main(sys.argv)
    except Exception as e:
        logging.fatal("Build failed due to %s", e)
        sys.exit(1)
    finally:
        end_time = time.monotonic()
        logging.info(
            "Completed in: %s", datetime.timedelta(seconds=end_time - start_time)
        )
