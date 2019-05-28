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
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import argparse
import datetime
import os
import platform
import sys
import subprocess
import time

from server_config import ServerConfig

from Queue import Queue
from threading import Thread

AOSP_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", ".."))
TOOLS = os.path.join(AOSP_ROOT, "tools")


start_time = time.time()


def _reader(pipe, queue):
    try:
        with pipe:
            for line in iter(pipe.readline, b""):
                queue.put((pipe, line[:-1]))
    finally:
        queue.put(None)


def log_line(prefix, line):
    passed = datetime.timedelta(seconds=time.time() - start_time)
    logging.info("%s %s| %s", passed, prefix, line.strip())


def log_std_out(proc, log_prefix):
    """Logs the output of the given process."""
    q = Queue()
    Thread(target=_reader, args=[proc.stdout, q]).start()
    Thread(target=_reader, args=[proc.stderr, q]).start()
    for _ in range(2):
        for _, line in iter(q.get, None):
            log_line(log_prefix, line)


def run(cmd, env, log_prefix):
    log_line(log_prefix, "=" * 120)
    log_line(log_prefix, " ".join(cmd))
    cmd_env = os.environ.copy()
    cmd_env.update(env)
    is_windows = (platform.system() == "Windows")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=is_windows,  # Make sure windows propagates ENV vars properly.
        cwd=AOSP_ROOT,
        env=cmd_env)

    log_std_out(proc, log_prefix)
    proc.wait()
    if proc.returncode != 0:
        raise Exception("Failed to run %s - %s" %
                        (" ".join(cmd), proc.returncode))


def install_deps():
    run([
        "python",
        os.path.join(AOSP_ROOT, "external", "qemu", "android", "build", "python",
                     "setup.py"), "develop", "--user"
    ], {}, "dep")


def main(argv):
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Configures the android emulator cmake project so it can be build"
    )
    parser.add_argument(
        "--out_dir", type=str, required=True, help="The ouput directory")
    parser.add_argument(
        "--dist_dir", type=str, required=True, help="The destination directory")
    parser.add_argument(
        "--build-id",
        type=str,
        default=[],
        required=True,
        dest="build_id",
        help="The emulator build number")
    parser.add_argument(
        "--target",
        type=str,
        default=platform.system(),
        help="The build target, defaults to current os")

    args = parser.parse_args()
    version = "{0[0]}.{0[1]}.{0[2]}".format(sys.version_info)
    log_line("inf", "Building on {} - {}, Python: {}".format(platform.system(),
                                                             platform.uname(),
                                                             version))

    target = platform.system().lower()
    if args.target:
        target = args.target.lower()

    if not os.path.isabs(args.out_dir):
        args.out_dir = os.path.join(AOSP_ROOT, args.out_dir)

    # Make sure we have all the build dependencies
    install_deps()

    # This how we are going to launch the python build script
    launcher = [
        "python",
        os.path.join(AOSP_ROOT, "external", "qemu", "android", "build", "python",
                     "cmake.py")
    ]

    # Standard arguments for both debug & production.
    args = [
        "--noqtwebengine", "--noshowprefixforinfo", "--out", args.out_dir,
        "--sdk_build_number", args.build_id, "--target", target, "--dist",
        args.dist_dir
    ]
    prod = ["--crash", "prod"]
    debug = ["--config", "debug"]

    # Kick of builds for 2 targets. (debug/release)
    with ServerConfig() as cfg:
        run(launcher + args + prod, {}, "rel")
        run(launcher + args + debug, {}, "dbg")

    log_line("inf", "Build completed!")


if __name__ == "__main__":
    main(sys.argv)
