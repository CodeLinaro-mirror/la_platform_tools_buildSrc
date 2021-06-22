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
import logging
import multiprocessing
import os
import platform
import site
import socket
import sys


if sys.version_info[0] == 3:
    from queue import Queue
else:
    from Queue import Queue

from distutils.dir_util import mkpath

from server_config import ServerConfig
from time_formatter import TimeFormatter
from qemu_builder import QemuBuilder

from utils import PYTHON_EXE, AOSP_ROOT, is_presubmit, run
from threading import currentThread


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


def config_logging():
    ch = logging.StreamHandler()
    ch.setFormatter(TimeFormatter("%(asctime)s %(threadName)s | %(message)s"))
    logging.root = logging.getLogger("build")
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(ch)
    currentThread().setName("inf")


def get_host_and_ip():
    """Try to get my hostname and ip address."""
    st = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        st.connect(("10.255.255.255", 1))
        my_ip = st.getsockname()[0]
    except Exception:
        my_ip = "127.0.0.1"
    finally:
        st.close()

    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "Unkwown"

    return hostname, my_ip


def log_system_info():
    """Log some useful system information."""
    version = "{0[0]}.{0[1]}.{0[2]}".format(sys.version_info)
    hostname, my_ip = get_host_and_ip()

    logging.info(
        "Hello from %s (%s). I'm a %s build bot", hostname, my_ip, platform.system()
    )
    logging.info("My uname is: %s", platform.uname())
    logging.info(
        "I'm hapy to build the emulator using Python %s (%s)",
        PYTHON_EXE,
        version,
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
        "--gfxstream", action="store_true", help="Build gfxstream libraries"
    )
    parser.add_argument("--crosvm", action="store_true", help="Build crosvm")
    parser.add_argument(
        "--generate", action="store_true", help="Generate and replaceqemu files only."
    )

    args = parser.parse_args()

    os.environ["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"

    target = platform.system().lower()

    if args.target:
        target = args.target.lower()

    crosscompile = target != platform.system().lower()

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
        # Unit tests on M1 are failing, so let's not run them yet
        cmd.append("--no-tests")
    else:
        prod = ["--crash", "prod"]

    if args.gfxstream:
        cmd.append(gfxstream_arg)
    if args.crosvm:
        cmd.append(crosvm_arg)

    # Make sure the dist directory exists.
    mkpath(args.dist_dir)

    # Kick of builds for 2 targets. (debug/release)
    with ServerConfig(is_presubmit(args.build_id)) as cfg:

        # Build qemu, and make sure the cmake file matches.
        bld = QemuBuilder(target, args.dist_dir, args.out_dir, cfg)
        if args.generate:
            bld.generate()
            return
        elif (crosscompile or target != 'darwin_aarch64'):
            bld.validate()
        else:
            logging.info("Not validating QEMU build.")

        run(launcher + cmd + prod, cfg.get_env(), "rel")
        if not args.gfxstream and not args.crosvm:
            run(launcher + cmd + debug, cfg.get_env(), "dbg")

    logging.info("Build completed!")


if __name__ == "__main__":
    main(sys.argv)
