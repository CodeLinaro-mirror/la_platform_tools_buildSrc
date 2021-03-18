#!/usr/bin/env python
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
import json
import logging
import multiprocessing
import os
import platform
import site
import subprocess
import sys

from distutils.spawn import find_executable
from Queue import Queue
from threading import Thread, currentThread

from server_config import ServerConfig
from time_formatter import TimeFormatter

AOSP_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..")
)
TOOLS = os.path.join(AOSP_ROOT, "tools")
PYTHON_EXE = sys.executable or "python"


def _reader(pipe, queue):
    try:
        with pipe:
            for line in iter(pipe.readline, b""):
                queue.put((pipe, line[:-1]))
    finally:
        queue.put(None)


def log_std_out(proc):
    """Logs the output of the given process."""
    q = Queue()
    Thread(target=_reader, args=[proc.stdout, q]).start()
    Thread(target=_reader, args=[proc.stderr, q]).start()
    for _ in range(2):
        for _, line in iter(q.get, None):
            logging.info(line)


def run(cmd, env, log_prefix, cwd=AOSP_ROOT):
    currentThread().setName(log_prefix)
    cmd_env = os.environ.copy()
    cmd_env.update(env)
    is_windows = platform.system() == "Windows"

    logging.info("=" * 140)
    logging.info(json.dumps(cmd_env, sort_keys=True))
    logging.info("%s $> %s", cwd, " ".join(cmd))
    logging.info("=" * 140)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=is_windows,  # Make sure windows propagates ENV vars properly.
        cwd=cwd,
        env=cmd_env,
    )

    log_std_out(proc)
    proc.wait()
    if proc.returncode != 0:
        raise Exception("Failed to run %s - %s" % (" ".join(cmd), proc.returncode))


def install_deps():
    # It is possible that the USER_SITE dir has never been created on freshly minted
    # windows build bots. Since python's setuptools doesn't create it for us, we do it
    # if needed.
    if not os.path.exists(site.USER_SITE):
        os.makedirs(site.USER_SITE)

    run(
        [PYTHON_EXE, "setup.py", "develop", "--user"],
        {},
        "dep",
        os.path.join(AOSP_ROOT, "external", "qemu", "android", "build", "python"),
    )


def is_presubmit(build_id):
    return build_id.startswith("P")


def config_logging():
    ch = logging.StreamHandler()
    ch.setFormatter(TimeFormatter("%(asctime)s %(threadName)s | %(message)s"))
    logging.root = logging.getLogger("build")
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(ch)
    currentThread().setName("inf")


def main(argv):
    config_logging()

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
        "--gfxstream", action="store_true", help="Build gfxstream libraries"
    )
    parser.add_argument("--crosvm", action="store_true", help="Build crosvm")

    args = parser.parse_args()
    version = "{0[0]}.{0[1]}.{0[2]}".format(sys.version_info)
    logging.info(
        "Building with %s on %s - %s, Python: %s",
        PYTHON_EXE,
        platform.system(),
        platform.uname(),
        version,
    )

    os.environ["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"

    target = platform.system().lower()
    if args.target:
        target = args.target.lower()

    if not os.path.isabs(args.out_dir):
        args.out_dir = os.path.join(AOSP_ROOT, args.out_dir)

    # Make sure we have all the build dependencies
    install_deps()

    # This how we are going to launch the python build script
    launcher = [
        PYTHON_EXE,
        os.path.join(
            AOSP_ROOT, "external", "qemu", "android", "build", "python", "cmake.py"
        ),
    ]

    gfxstream_arg = "--gfxstream"
    crosvm_arg = "--crosvm"

    # Standard arguments for both debug & production.
    if args.qtwebengine:
        qtwebengine_arg = "--qtwebengine"
    else:
        qtwebengine_arg = "--noqtwebengine"
    cmd = [
        qtwebengine_arg,
        "--noshowprefixforinfo",
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

    debug = ["--config", "debug"]
    if target == "darwin_aarch64":
        prod = ["prod"]
    else:
        prod = ["--crash", "prod"]

    if args.gfxstream:
        cmd.append(gfxstream_arg)
    if args.crosvm:
        cmd.append(crosvm_arg)

    # Kick of builds for 2 targets. (debug/release)
    with ServerConfig(is_presubmit(args.build_id)) as cfg:
        run(launcher + cmd + prod, cfg.get_env(), "rel")
        if not args.gfxstream and not args.crosvm:
            run(launcher + cmd + debug, cfg.get_env(), "dbg")
            if target == "linux":
              run(
                  [
                      os.path.join(
                          AOSP_ROOT,
                          "external",
                          "qemu",
                          "android",
                          "scripts",
                          "unix",
                          "build-qemu-android.sh",
                      ),
                      "--build-dir={}/build-qemu".format(args.out_dir),
                      "--host=linux-x86_64",
                      "--verbose",
                  ],
                  cfg.get_env(),
                  "qemu"
              )

    logging.info("Build completed!")


if __name__ == "__main__":
    main(sys.argv)
