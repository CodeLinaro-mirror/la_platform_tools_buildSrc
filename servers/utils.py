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
import logging
import os
import platform
import subprocess
from pathlib import Path

from log_handler import LogHandler


current_file = Path(__file__).resolve()

AOSP_ROOT = current_file.parents[3]
BAZEL = (
    AOSP_ROOT / "prebuilts" / "bazel" / f"{platform.system().lower()}-x86_64" / "bazel"
)
TARGET_MAP = {
    "windows": "windows-x86_64",
    "linux": "linux-x86_64",
    "darwin": "darwin-x86_64",
}


class CommandFailedException(Exception):
    """Exception raised when the command fails."""


def run(cmd, env, cwd=AOSP_ROOT, throw_on_failure=True):
    """
    Run a command with the provided environment settings.

    Args:
        cmd (list): The command to be executed.
        env (dict): The environment settings to be used.
        cwd (str): The current working directory. Defaults to AOSP_ROOT.
        throw_on_failure (bool): Whether to raise an exception on command failure.

    Raises:
        CommandFailedException: If the command fails and throw_on_failure is True.
    """
    cmd_env = os.environ.copy()
    cmd_env.update(env)
    is_windows = platform.system() == "Windows"

    cmd = [str(x) for x in cmd]
    logging.info("%s $> %s", cwd, " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=is_windows,  # Make sure Windows propagates ENV vars properly.
        cwd=cwd,
        env=cmd_env,
    )

    log_handler = LogHandler()
    log_handler.start_log_proc(proc)

    proc.wait()
    if proc.returncode != 0 and throw_on_failure:
        raise CommandFailedException(f"{' '.join(cmd)} Status: {proc.returncode} != 0")
