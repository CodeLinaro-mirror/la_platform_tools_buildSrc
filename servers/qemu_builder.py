#!/usr/bin/env python
#
# Copyright 2021 - The Android Open Source Project
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
import hashlib
import logging
import os
import platform
from functools import partial

from utils import AOSP_ROOT, PYTHON_EXE, platform_to_cmake_target, run
import subprocess
import shutil
import sys


class QemuBuilder(object):
    """A qemu builder builds standard qemu, and extracts the cmake include file from the build.


       Note: Qemu 2.12 is still based on Python2.
    """

    _QEMU_DIR = os.path.join(AOSP_ROOT, "external", "qemu")

    _QEMU_BUILD_SCRIPT = os.path.join(
        _QEMU_DIR,
        "android",
        "scripts",
        "unix",
        "build-qemu-android.sh",
    )

    _CMAKE_GEN_SCRIPT = os.path.join(
        _QEMU_DIR,
        "android-qemu2-glue",
        "scripts",
        "gen-cmake.py",
    )

    def __init__(self, target, dist_dir, out_dir, cfg):
        self.os = target
        self.aarch = "x86_64"

        if target == "linux_aarch64":
            self.os = "linux"
            self.aarch = "aarch64"
        if (target == "darwin" and platform.machine() == "arm64") or (
            target == "darwin_aarch64"
        ):
            self.os = "darwin"
            self.aarch = "aarch64"
        if target == "windows":
            self.os = "windows_msvc"
            self.aarch = "x86_64"

        self.target = target
        self.host = platform_to_cmake_target(target)
        self.out = os.path.join(out_dir, "qemu_build")
        self.dist = dist_dir
        self.env = cfg.get_env()

    def build(self):
        run(
            [
                QemuBuilder._QEMU_BUILD_SCRIPT,
                "--build-dir={}".format(self.out),
                "--host={}".format(self.host),
                "--verbose",
                "--verbose",
            ],
            self.env,
            "qemu",
        )

    def gen_cmake(self):
        run(
            [
                PYTHON_EXE,
                QemuBuilder._CMAKE_GEN_SCRIPT,
                "-r",
                os.path.join(AOSP_ROOT, "external", "qemu"),
                "-i",
                self.out,
                "-o",
                self.dist,
                "-s",
                self.host,
                "-v",
            ],
            self.env,
            "qemu",
        )

    def can_validate(self):
        machine = platform.system().lower()
        if platform.machine() == "arm64":
            machine += "_aarch64"

        crosscompile = self.target != machine

        if crosscompile:
            return True

        # We need posix to build qemu
        return self.target != "windows"

    def _sha256(self, fname):
        sha = hashlib.sha256()
        with open(fname, "rb") as f:
            for chunk in iter(partial(f.read, 1024 * 64), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def _compare_file(self, src, expected):
        try:
            cmd = subprocess.check_output(["diff", "-w", src, expected])
        except subprocess.CalledProcessError as err:
            logging.error(
                "Please apply the changes below, or copy the updated {}, from artifacts:".format(
                    expected
                )
            )
            logging.error(
                "-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-="
            )
            sys.stderr.write(
                "patch  --ignore-whitespace {} << 'EOF'\n".format(expected)
            )
            sys.stderr.write(err.output.decode("utf-8"))
            sys.stderr.write("EOF\n")
            raise Exception(
                "Mismatch of expected: {0} != {1}.\ncp {0} {1}\n".format(expected, src)
            )

    def compare_cmake(self):
        # Check that config host is still like it should be.
        config_host = os.path.join(
            QemuBuilder._QEMU_DIR,
            "android-qemu2-glue",
            "config",
            "{}-{}".format(self.os, self.aarch),
            "config-host.h",
        )
        expected = os.path.join(
            self.out,
            "build-{}-{}".format(self.os, self.aarch),
            "qemu-android",
            "config-host.h",
        )

        # The config file generation differs from platform to platform
        # self._compare_file(config_host, expected)

        cmake_inc = "cmake-main.{}.inc".format(self.host)
        src = os.path.join(QemuBuilder._QEMU_DIR, cmake_inc)
        # Expected is the autogenerated cmake include from the
        # current qemu build.
        expected = os.path.join(self.dist, cmake_inc)
        self._compare_file(src, expected)

    def generate(self):
        self.build()
        self.gen_cmake()
        config_host = os.path.join(
            QemuBuilder._QEMU_DIR,
            "android-qemu2-glue",
            "config",
            "{}-{}".format(self.os, self.aarch),
            "config-host.h",
        )
        expected = os.path.join(
            self.out,
            "build-{}-{}".format(self.os, self.aarch),
            "qemu-android",
            "config-host.h",
        )
        logging.info("cp %s %s", expected, config_host)
        shutil.copy(expected, config_host)
        cmake_inc = "cmake-main.{}.inc".format(self.host)
        src = os.path.join(QemuBuilder._QEMU_DIR, cmake_inc)
        expected = os.path.join(self.dist, cmake_inc)
        logging.info("cp %s %s", expected, src)
        shutil.copy(expected, src)

    def validate(self):
        if not self.can_validate():
            logging.info("Cannot validate qemu build.. skipping")
            return

        logging.info("Validating qemu..")
        self.build()
        self.gen_cmake()
        self.compare_cmake()
