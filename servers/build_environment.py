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
import argparse
import json
import logging
import os
import platform
import subprocess
import time
import pathlib
from typing import Dict, List, Union

from log_handler import LogHandler

try:
    import winreg
except ModuleNotFoundError:
    # Winreg is a windows only thing..
    pass

current_file = pathlib.Path(__file__).resolve()

AOSP_ROOT = current_file.parents[3]


def disable_debug_policy():
    """Disable the debug policy on Windows system to prevent debug UI from being activated."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting",
        ) as registry_key:
            winreg.SetValue(registry_key, "DontShowUI", 1)
    except OSError as err:
        logging.error("Failed to modify key, error: %s.", err)

    # Next clear out the just in time debuggers.
    to_delete = [
        (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug", "Debugger"),
        (
            r"SOFTWARE\Wow6432Node\Microsoft\Windows NT\CurrentVersion\AeDebug",
            "Debugger",
        ),
    ]
    for current_key, entry in to_delete:
        try:
            # See https://docs.microsoft.com/en-us/visualstudio/debugger/debug-using-the-just-in-time-debugger?view=vs-2019#disable-just-in-time-debugging-from-the-windows-registry)
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, current_key, 0, winreg.KEY_ALL_ACCESS
            ) as open_key:
                winreg.DeleteValue(open_key, entry)
        except OSError as err:
            logging.error("Failed to remove key, error: %s.", err)


class CommandFailedException(Exception):
    """Exception raised when the command fails."""


class BuildEnvironment:
    """Class that configures the environment and tracks the time it takes to run the build."""

    args: argparse.Namespace
    is_presubmit: bool
    target: str
    is_windows: bool
    _start_time: float
    _cmd_env: Dict[str, str]
    _log_handler: LogHandler

    def __init__(self, args):
        self.args = args
        self.is_presubmit = args.build_id.startswith("P")
        self.target = platform.system().lower()
        self.is_windows = self.target == "windows"
        self._start_time = time.time()
        self._cmd_env = os.environ.copy()
        self._cmd_env["PYTHONUNBUFFERED"] = "1"
        self._log_handler = LogHandler()

    def get_env(self):
        """Gets the OS environment that should be used when running a program."""
        return self._cmd_env

    def __enter__(self):
        """Configure windows policy if needed."""
        # On windows we do not want debug ui to be activated.
        if self.is_windows:
            disable_debug_policy()

        logging.info("=" * 140)
        logging.info(json.dumps(self._cmd_env, sort_keys=True))
        logging.info("=" * 140)

        self._start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        end_time = time.time()
        execution_time = end_time - self._start_time

        # Format the execution time nicely
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(execution_time))
        logging.info("Completed build in %s", formatted_time)

    def run(
        self,
        cmd: List[str],
        env: Dict[str, str] = None,
        cwd: Union[pathlib.Path, str] = AOSP_ROOT,
        throw_on_failure: bool = True,
        timeout: Union[float, None] = 3600,
    ):
        """
        Run a command with the build environment settings.

        Args:
            cmd: The command to be executed.
            env: Extra environment variables.
            cwd: The current working directory. Defaults to AOSP_ROOT.
            throw_on_failure: Whether to raise an exception on command failure.
            timeout: Timeout of the command.

        Raises:
            CommandFailedException: If the command fails and throw_on_failure is True.
        """
        if not env:
            env = {}
        env.update(self.get_env())

        cmd = [str(x) for x in cmd]
        logging.info("%s $> %s", cwd, " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=self.is_windows,  # Make sure Windows propagates ENV vars properly.
            cwd=cwd,
            env=env,
        )
        self._log_handler.start_log_proc(proc)

        try:
            proc.wait(timeout=timeout)
            if proc.returncode != 0 and throw_on_failure:
                raise CommandFailedException(
                    f"Failed to run {' '.join(cmd)}, exit code: {proc.returncode}"
                )
            return proc.returncode
        except subprocess.TimeoutExpired as timeout1:
            logging.error(
                "The command %s timed out after %s seconds, terminating",
                " ".join(cmd),
                timeout1.timeout,
            )
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired as timeout2:
                logging.error(
                    "Command %s did not terminate after %s seconds, killing",
                    " ".join(cmd),
                    timeout2.timeout,
                )
                proc.kill()
            raise
