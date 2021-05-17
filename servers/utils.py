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
import json
import logging
import os
import platform
import subprocess
import sys
from functools import partial

if sys.version_info[0] == 3:
    from queue import Queue
else:
    from Queue import Queue

from threading import Thread, currentThread

AOSP_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..")
)
TOOLS = os.path.join(AOSP_ROOT, "tools")
PYTHON_EXE = sys.executable or "python"
TARGET_MAP = {
    "windows": "windows_msvc-x86_64",
    "linux": "linux-x86_64",
    "darwin": "darwin-x86_64",
    "linux_aarch64": "linux-aarch64",
    "darwin_aarch64": "darwin-aarch64",
}


def platform_to_cmake_target(target):
    """Translates platform to cmake target"""
    return TARGET_MAP[target]


def is_presubmit(build_id):
    return build_id.startswith("P")


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

    _log_proc(proc, log_prefix)
    proc.wait()
    if proc.returncode != 0:
        raise Exception("Failed to run %s - %s" % (" ".join(cmd), proc.returncode))


def log_to_queue(q, line):
    """Logs the output of the given process."""
    if q.full():
        q.get()

    strip = line.strip()
    logging.info(strip)
    q.put(strip)


def _reader(pipe, logfn):
    try:
        with pipe:
            for line in iter(pipe.readline, b""):
                lg = line[:-1]
                if sys.version_info[0] == 3:
                    lg = lg.decode("utf-8")
                logfn(lg.strip())
    finally:
        pass


def _log_proc(proc, log_prefix):
    """Logs the output of the given process."""
    q = Queue()
    for args in [[proc.stdout, logging.info], [proc.stderr, logging.error]]:
        t = Thread(target=_reader, args=args)
        t.setName(log_prefix)
        t.start()

    return q
