# Copyright 2022 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List
from utils import run

OS_NAME = platform.system().lower()
HERE = Path(os.path.dirname(__file__)).absolute()
AOSP_ROOT = HERE.parents[2]
SDK_EMULATOR = (
    AOSP_ROOT / "prebuilts" / "android-emulator-build" / "system-images" / OS_NAME
)
JDK_ROOT = AOSP_ROOT / "prebuilts" / "studio" / "jdk" / "jdk17"
ANDROID_SDK_ROOT = SDK_EMULATOR
PYTHON_DIR = AOSP_ROOT / "prebuilts" / "python" / f"{OS_NAME}-x86"
if OS_NAME != "windows":
    PYTHON = PYTHON_DIR / "bin" / "python3"
else:
    PYTHON = PYTHON_DIR / "python.exe"

class NoXServer(Exception):
    pass


class JavaNotFound(Exception):
    pass


class PyRunner:
    """PyRunner

    A class that provides a convenient way to run python commands.
    It sets up the environment with the required variables,
    installs packages using pip, and runs the specified command
    with the given arguments and environment variables.

    Attributes:
        env (dict[str, str]): Environment variables for the command.
        This includes ANDROID_SDK_ROOT, ANDROID_HOME, and
        JAVA_HOME. If the platform is Linux, DISPLAY will also be included.

        py_exe (str): The path to the python interpreter.
    """

    def __init__(self, skip_display_init):
        """Initialize PyRunner with the environment variables required
        for running the Python command.

        The environment variables include `ANDROID_SDK_ROOT`, `ANDROID_HOME` and `JAVA_HOME`.
        If the platform is Linux,  an attempt will be made to find or
        launch a vnc server and set its display as the value for the `DISPLAY`
        environment variable, unless `skip_display_init` is True.
        """
        self.env = {
            "ANDROID_SDK_ROOT": str(ANDROID_SDK_ROOT),
            "ANDROID_HOME": str(ANDROID_SDK_ROOT),
            "JAVA_HOME": self._get_java_home(),
            # Make sure adb and java are on the path.
            "PATH": f"{self._get_jdk_path()}"
            + f"{os.pathsep}{ANDROID_SDK_ROOT / 'platform-tools'}"
            + f"{os.pathsep}{os.environ['PATH']}",
        }
        self.py_exe = shutil.which("python")

        if not skip_display_init:
            if platform.system() == "Linux":
                try:
                    display = self._get_X_Display()
                except NoXServer as xerr:
                    logging.warning(
                        "No X server available (%s), attempting to launch a vnc server",
                        xerr,
                    )
                    subprocess.check_call("vncserver")
                    display = self._get_X_Display()

                self.env["DISPLAY"] = display

        logging.info("Using environment: %s", self.env)

    def _get_jdk_path(self):
        """Gets the path to java + javac from AOSP"""
        jdk_map = {
            "windows": JDK_ROOT / "win" / "bin",
            "linux": JDK_ROOT / "linux" / "bin",
            "darwin-arm64": JDK_ROOT / "mac-arm64" / "Contents" / "Home" / "bin",
            "darwin-x86_64": JDK_ROOT / "mac" / "Contents" / "Home" / "bin",
        }
        jdk = jdk_map.get(OS_NAME, None)
        if OS_NAME == "darwin":
            jdk = jdk_map.get(f"{OS_NAME}-{platform.machine()}")
        return f"{jdk}"

    def _get_java_home(self):
        """Retrieves the path to the Java home directory from the active Java interpreter.

        Returns:
            str: Path to the Java home directory

        Raises:
            JavaNotFound: If no `java` interpreter is found on the system path.
        """
        jdk = self._get_jdk_path()
        java = shutil.which("java", path=jdk)
        if not java:
            raise JavaNotFound(
                f"No `java` interpreter on the path ({jdk}). Java is required for "
                + "creating the APK's used by the test."
            )

        is_windows = platform.system() == "Windows"
        status = subprocess.run(
            [java, "-XshowSettings:properties", "-version"],
            encoding="utf-8",
            capture_output=True,
            shell=is_windows,
            check=True,
        )
        java_home = [
            line.strip() for line in status.stderr.splitlines() if "java.home" in line
        ][0]

        return java_home.split("=")[1].strip()

    def _is_x_running(self, display: str) -> bool:
        """Checks if X server is running on specific display

        Args:
          display (str): the display name

        Return:
          bool: True if X is running, False otherwise
        """
        return (
            subprocess.run(["xset", "-display", display, "-q"], check=False).returncode
            == 0
        )

    def _get_X_Display(self) -> str:
        """Finds a working DISPLAY environment variable that is backed by a working
        X Server. This can launch a VNCServer if needed.

        Raises:
            NoXServer: If no working X server can be found.

        Returns:
            str: Value for the DISPLAY environment variable (i.e. ":XDISPLAY")
        """
        xdir = Path("/tmp/.X11-unix")
        if not xdir.exists():
            raise NoXServer(
                f"The directory {xdir} does not exist, no X server available."
            )

        for xdisplay in xdir.glob("X*"):
            display = f":{xdisplay.name[1:]}"
            logging.info("Checking to see if X11 is available at DISPLAY=%s", display)
            if self._is_x_running(display):
                return display

        raise NoXServer("Unable to find a working XServer")

    def pip_install(self, packages: [str]):
        """installs the specified packages using pip"

        Args:
            packages ([str]): The set of packages to install
        """
        self.run(["-m", "pip", "install", "-v", "--upgrade"] + packages)

    def run(
        self,
        args: List[str],
        env: Dict[str, str] = {},
        timeout: int = 300,
        cwd: str = os.getcwd(),
        check_output: bool = True,
        log_prefix: str = "--",
    ) -> int:
        """Runs a Python command with the specified arguments, environment variables, and timeout.

        Args:
            args (List[str]): Set of arguments to give to the Python interpreter.
            env (Dict[str, str]): Optional environment variables to use.
            timeout (int): Optional timeout in seconds to apply to the command execution.
            cwd (str): The working directory to use for the command execution.
            check_output (bool): Set to True if a non-zero exit code should raise an exception.

        Returns:
            int: The exit code of the process.
        """
        emu_env = self.env.copy()
        emu_env.update(env)
        if env:
            logging.info("Using %s from %s", emu_env, self.env)
        return run(
            [self.py_exe] + args,
            env=emu_env,
            cwd=cwd,
            throw_on_failure=check_output,
            log_prefix=log_prefix
        )


class AospPyRunner(PyRunner):
    """AospPyRunner is a PyRunner that uses the Python interpreter that is in AOSP

    This python interpreter does not have SSL and hence has a series of limitations.
    This runner tries to minimize the impact of these limitations, by:

    - Creating a virtual environment in posix
    - Patch the windows interpreter to work with the pytests.
    """

    def __init__(self, repo, skip_display_init=False, in_directory=None):
        super().__init__(skip_display_init)
        self.repo = repo
        self.in_directory = in_directory
        if not in_directory:
            self.tmp = tempfile.TemporaryDirectory()
            self.in_directory = self.tmp.name

        if platform.system() == "Windows":
            self._fixup_windows_py3_dll()
            run(
                [
                    PYTHON,
                    AOSP_ROOT / "external" / "adt-infra" / "devpi" / "get-pip.py",
                    "--no-wheel",
                    "--no-setuptools",
                    "--index-url",
                    f"{repo}",
                ],
                env=self.env,
                log_prefix="setup",
            )
            self.py_exe = PYTHON
            self.run(
                [
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "virtualenv",
                    "--index-url",
                    f"{self.repo}",
                ]
            )
            self.env["PYTHONPATH"] = str(HERE / "hacks")
            virtualenv = "virtualenv"
        else:
            virtualenv = "venv"

        tmpdir = Path(self.in_directory)
        run(
            [
                PYTHON,
                "-m",
                virtualenv,
                tmpdir / ".venv",
            ],
            env=self.env,
            log_prefix="setup",
        )

        if platform.system() == "Windows":
            self.py_exe = tmpdir / ".venv" / "Scripts" / "python"
        else:
            self.py_exe = tmpdir / ".venv" / "bin" / "python"

        self.env["VIRTUAL_ENV"] = str(tmpdir / ".venv")

        self.run(
            [
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "wheel",
                "setuptools",
                "--index-url",
                f"{self.repo}",
            ]
        )

    def _fixup_windows_py3_dll(self):
        # Fixup incorrect dll in windows see b/265843618.
        py310dll = PYTHON_DIR / "python310.dll"
        py3dll = PYTHON_DIR / "Python3.dll"
        assert (
            py310dll
        ).exists(), (
            "python310.dll does not exist, did you upgrade the python interpreter?"
        )
        if not py3dll.exists():
            py3dll.symlink_to(py310dll)

    def pip_install(self, packages: [str]):
        """installs the specified packages using pip"

        Args:
            packages ([str]): The set of packages to install
        """
        super().pip_install(["--index-url", f"{self.repo}"] + packages)
