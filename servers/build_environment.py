# Copyright 2021 - The Android Open Source Project
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

import argparse
import asyncio
import getpass
import json
import logging
import os
import platform
import subprocess
import sys
import time
import pathlib
from typing import Dict, List, Optional, Union, Callable

_SYSTEM_TO_TARGET = {
    "linux": "linux_x64",
    "darwin": "mac_aarch64",
    "windows": "windows_x64",
}


def default_target() -> str:
    """Returns the default build target based on the current system."""
    return _SYSTEM_TO_TARGET[platform.system().lower()]


try:
    import winreg
except ModuleNotFoundError:
    # Winreg is a windows only thing..
    pass

_REPO_MARKERS = ("MODULE.bazel", "REPO.bazel", "WORKSPACE", "WORKSPACE.bazel")


def find_repo_root(env: Dict[str, str]) -> pathlib.Path:
    # Fast path when building on CI
    if "BUILD_WORKSPACE_DIRECTORY" in env:
        return pathlib.Path(env["BUILD_WORKSPACE_DIRECTORY"])
    root = pathlib.Path(__file__).resolve().parent
    while not any((root / x).exists() for x in _REPO_MARKERS):
        root = root.parent
    return root


def _getuser() -> str:
    """Gets the logged in user.

    Fallback to os.getlogin() since getpass on Windows may raise an exception.
    """
    try:
        return getpass.getuser()
    except:
        return os.getlogin()


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


async def _read_stream(
    stream: asyncio.StreamReader, log_fn: Optional[Callable[[str], None]]
) -> str:
    """Asynchronously reads lines from a stream and either logs (if log_fn is not None) or returns them."""
    output = []
    try:
        while not stream.at_eof():
            line = await stream.readline()
            line = line.decode("utf-8", errors="replace")
            if not log_fn:
                output.append(line)
            elif line:
                log_fn(line.rstrip())
    except Exception as e:
        logging.exception("Stream closed unexpectedly: %s", e)
        if output:
            logging.warning("--- PARTIAL OUTPUT CAPTURED BEFORE FAILURE ---")
            for line in output:
                logging.warning(line.rstrip())
            logging.warning("--------------------------------------------")
        raise
    return "".join(output)


class CommandFailedException(Exception):
    """Exception raised when the command fails."""

    result: subprocess.CompletedProcess

    def __init__(self, msg: str, result: subprocess.CompletedProcess):
        super().__init__(msg)
        self.result = result


class BazelEnvironment:
    """Manages the environment for Bazel builds, including execution and logging.

    This class sets up the environment, tracks execution time, and provides
    a unified interface for running commands with appropriate logging and
    error handling. It is intended to be used as a context manager.

    Attributes:
        build_target: The Bazel build target, if available.
        host_platform: The current operating system (lowercase).
        target_platform: The build target platform.
        repo_root: The root directory of the repository.
        user: The current username.
        tmp_dir:  System temporary directory (from $TMPDIR, if defined).
    """

    build_target: Optional[str]
    host_platform: str
    target_platform: str
    repo_root: pathlib.Path
    user: str
    tmp_dir: Optional[pathlib.Path]

    _start_time: float
    _cmd_env: Dict[str, str]

    def __init__(
        self,
        args: argparse.Namespace,
        env: Dict[str, str] = os.environ,
        repo_root: pathlib.Path = find_repo_root(os.environ),
        user: str = _getuser(),
    ):
        """Initializes the BazelEnvironment.

        Args:
            args: Parsed command-line arguments.
            env: The environment variables. Defaults to os.environ.
            repo_root: The root of the repository. Defaults to finding it.
            user: The current user. Defaults to the logged-in user.
        """
        self.build_target = env.get("BUILD_TARGET_NAME")
        self.host_platform = platform.system().lower()
        self.target_platform = (
            args.target if hasattr(args, "target") else default_target()
        )
        self.repo_root = repo_root
        self.user = user
        self.tmp_dir = (
            pathlib.Path(os.environ.get("TMPDIR")) if os.environ.get("TMPDIR") else None
        )

        self._start_time = time.time()
        self._cmd_env = env.copy()
        self._cmd_env["PYTHONUNBUFFERED"] = "1"

    def get_env(self):
        """Gets the OS environment that should be used when running a program."""
        return self._cmd_env

    def __enter__(self):
        """Configure platform specific settings if needed."""
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

    def is_windows(self):
        return False

    async def _run_async(
        self,
        cmd: List[str],
        *,
        capture_output: bool = False,
        env: Dict[str, str] = None,
        cwd: Optional[Union[pathlib.Path, str]] = None,
        throw_on_failure: bool = True,
        timeout: Union[float, None] = 3600,
    ) -> subprocess.CompletedProcess:
        """
        Asynchronously runs a command, with a choice to stream or capture output.
        """
        if not env:
            env = {}
        env.update(self.get_env())
        if not cwd:
            cwd = self.repo_root

        cmd_str = [str(x) for x in cmd]
        logging.info("%s $> %s", cwd, " ".join(cmd_str))

        proc = await asyncio.create_subprocess_exec(
            *cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            async with asyncio.TaskGroup() as tg:
                stdout_task = tg.create_task(
                    _read_stream(proc.stdout, None if capture_output else logging.info)
                )
                stderr_task = tg.create_task(
                    _read_stream(proc.stderr, None if capture_output else logging.error)
                )
                tg.create_task(
                    asyncio.wait_for(proc.wait(), timeout=timeout)
                )
        except* asyncio.TimeoutError:
            logging.error("Command timed out after %s seconds, terminating.", timeout)
            raise subprocess.TimeoutExpired(cmd, timeout)
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=30)
                except asyncio.TimeoutError:
                    logging.error("Command did not terminate after 30s, killing.")
                    proc.kill()

        stdout = stdout_task.result() or None
        stderr = stderr_task.result() or None
        result = subprocess.CompletedProcess(
            args=cmd_str, returncode=proc.returncode, stdout=stdout, stderr=stderr
        )

        if proc.returncode != 0 and throw_on_failure:
            msg = [f"Failed to run {' '.join(cmd_str)}, exit code: {proc.returncode}"]
            raise CommandFailedException("\n".join(msg), result)

        return result

    def run(
        self,
        cmd: List[str],
        *,
        capture_output: bool = False,
        env: Dict[str, str] = None,
        cwd: Optional[Union[pathlib.Path, str]] = None,
        throw_on_failure: bool = True,
        timeout: Union[float, None] = 3600,
    ) -> subprocess.CompletedProcess:
        """Runs a command with the configured environment.

        Args:
            cmd: The command to be executed.
            capture_output: Whether to capture stdout and stderr, instead of
                logging them.
            env: Extra environment variables.
            cwd: The current working directory. Defaults to the repo root.
            throw_on_failure: Whether to raise an exception on command failure.
            timeout: Timeout of the command.

        Raises:
            CommandFailedException: If the command fails and throw_on_failure is True.
        """
        return asyncio.run(
            self._run_async(
                cmd,
                capture_output=capture_output,
                env=env,
                cwd=cwd,
                throw_on_failure=throw_on_failure,
                timeout=timeout,
            )
        )


class BuildEnvironment(BazelEnvironment):
    """Extends BazelEnvironment with build-specific settings.

    Adds build-specific attributes like build ID, presubmit status,
    distribution directory, and Crashpad integration for symbol uploads.
    Intended to be used as a context manager.

    Attributes:
        build_id: The ID of the current build (e.g., from Jenkins).
        is_presubmit: True if the build is a presubmit (CI) build.
        dist_dir: The directory for build artifacts.
        crashpad_symbol_server_key: API key for Crashpad symbol server.
        crashpad_server: URL of the Crashpad symbol server.
    """

    build_id: str
    is_presubmit: bool
    dist_dir: pathlib.Path
    crashpad_symbol_server_key: str
    crashpad_server: str

    CRASHPAD_PROD = "https://prod-crashsymbolcollector-pa.googleapis.com"
    CRASHPAD_STAGING = "https://staging-crashsymbolcollector-pa.googleapis.com"

    def __init__(
        self,
        args: argparse.Namespace,
        env: Dict[str, str] = os.environ,
        repo_root: pathlib.Path = find_repo_root(os.environ),
        user: str = _getuser(),
    ):
        """Initializes the BuildEnvironment.

        Args:
            args: Parsed command-line arguments.
            env: The environment variables. Defaults to os.environ.
            repo_root: The root of the repository.  Defaults to finding it.
            user: The current user. Defaults to the logged-in user.
        """
        super().__init__(args, env, repo_root, user)
        self.build_id = args.build_id if hasattr(args, "build_id") else "PNONE"
        self.build_target = env.get("BUILD_TARGET_NAME")
        self.is_presubmit = self.build_id.startswith("P")
        self.dist_dir = (
            pathlib.Path(args.dist) if hasattr(args, "build_id") else "PNONE"
        )

        self.crashpad_symbol_server_key = os.environ.get("EMULATOR_SYMBOL_SERVER_KEY")
        self.crashpad_server = self.CRASHPAD_PROD
        if self.is_presubmit:
            self.crashpad_server = self.CRASHPAD_STAGING

        if not self.crashpad_symbol_server_key:
            key_path = pathlib.Path.home() / ".emulator_symbol_server_key"
            try:
                with open(key_path, "r") as f:
                    self.crashpad_symbol_server_key = f.read().strip()
            except FileNotFoundError:
                logging.error("Error: Symbol server key file not found at %s", key_path)

    def __enter__(self):
        self.dist_dir.mkdir(exist_ok=True, parents=True)
        return super().__enter__()


class LinuxBazelEnvironment(BazelEnvironment):
    """Linux-specific Bazel environment configuration.

    Attributes:
        python: Path to the Python executable.
    """

    python: pathlib.Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        python_dir = (
            self.repo_root / "prebuilts" / "python" / f"{self.host_platform}-x86"
        )
        self.python = python_dir / "bin" / "python3"

        if not self.python.exists():
            self.python = pathlib.Path(sys.executable)


class WindowsBazelEnvironment(BazelEnvironment):
    """Windows-specific Bazel environment configuration.

    Attributes:
        python: Path to the Python executable.
    """

    python: pathlib.Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        python_dir = (
            self.repo_root / "prebuilts" / "python" / f"{self.host_platform}-x86"
        )
        self.python = python_dir / "python.exe"

        if not self.python.exists():
            self.python = pathlib.Path(sys.executable)

    def __enter__(self):
        """Configure windows policy if needed."""
        # On windows we do not want debug ui to be activated.
        disable_debug_policy()
        return super().__enter__()

    def is_windows(self):
        return True


class MacOSBazelEnvironment(BazelEnvironment):
    """macOS-specific Bazel environment configuration.

    Attributes:
        python: Path to the Python executable.
    """

    python: pathlib.Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        python_dir = (
            self.repo_root / "prebuilts" / "python" / f"{self.host_platform}-x86"
        )
        self.python = python_dir / "bin" / "python3"

        if not self.python.exists():
            self.python = pathlib.Path(sys.executable)

        xcode_path = subprocess.check_output(["xcode-select", "-p"], text=True).strip()
        self._cmd_env["DEVELOPER_DIR"] = xcode_path


class WindowsBuildEnvironment(WindowsBazelEnvironment, BuildEnvironment):
    """Environment for Windows builds, combining Windows and build settings."""

    def __init__(self, *args, **kwargs):
        WindowsBazelEnvironment.__init__(self, *args, **kwargs)
        BuildEnvironment.__init__(self, *args, **kwargs)


class LinuxBuildEnvironment(LinuxBazelEnvironment, BuildEnvironment):
    """Environment for Linux builds, combining Linux and build settings."""

    def __init__(self, *args, **kwargs):
        LinuxBazelEnvironment.__init__(self, *args, **kwargs)
        BuildEnvironment.__init__(self, *args, **kwargs)


class MacOSBuildEnvironment(MacOSBazelEnvironment, BuildEnvironment):
    """Environment for macOS builds, combining macOS and build settings."""

    def __init__(self, *args, **kwargs):
        MacOSBazelEnvironment.__init__(self, *args, **kwargs)
        BuildEnvironment.__init__(self, *args, **kwargs)


def create_build_environment(args: argparse.Namespace) -> BuildEnvironment:
    """Factory function to create the appropriate BuildEnvironment subclass."""
    host_platform = platform.system().lower()
    if host_platform == "windows":
        return WindowsBuildEnvironment(args)
    elif host_platform == "linux":
        return LinuxBuildEnvironment(args)
    elif host_platform == "darwin":  # macOS
        return MacOSBuildEnvironment(args)
    else:
        raise ValueError(f"Unsupported platform: {host_platform}")


def create_bazel_environment(args: argparse.Namespace) -> BuildEnvironment:
    """Factory function to create the appropriate BazelEnvironment subclass."""
    host_platform = platform.system().lower()
    if host_platform == "windows":
        return WindowsBazelEnvironment(args)
    elif host_platform == "linux":
        return LinuxBazelEnvironment(args)
    elif host_platform == "darwin":  # macOS
        return MacOSBazelEnvironment(args)
    else:
        raise ValueError(f"Unsupported platform: {host_platform}")
