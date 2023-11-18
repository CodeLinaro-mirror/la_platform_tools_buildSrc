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
import time

try:
    import winreg
except ModuleNotFoundError:
    # Winreg is a windows only thing..
    pass


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


class BuildEnvironment:
    """Class that configures the environment and tracks the time it takes to run the build."""

    def __init__(self, args):
        self.args = args
        self.presubmit = args.build_id.startswith("P")
        self.target = platform.system().lower()
        self.start_time = time.time()
        self.cmd_env = os.environ.copy()
        self.cmd_env["PYTHONUNBUFFERED"] = "1"

    def get_env(self):
        """Gets the OS environment that should be used when running a program."""
        return self.cmd_env

    def __enter__(self):
        """Configure windows policy if needed."""
        # On windows we do not want debug ui to be activated.
        if self.target == "windows":
            disable_debug_policy()

        logging.info("=" * 140)
        logging.info(json.dumps(self.cmd_env, sort_keys=True))
        logging.info("=" * 140)

        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        end_time = time.time()
        execution_time = end_time - self.start_time

        # Format the execution time nicely
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(execution_time))
        logging.info("Completed build in %s", formatted_time)
