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
import logging
import os
import pathlib
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

try:
    from . import host_resources
except ImportError:
    from host import host_resources

try:
    from services.log_handler import mask_command, redact_secrets, register_secret
except ImportError:
    from log_handler import mask_command, redact_secrets, register_secret

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


def find_repo_root(env: dict[str, str]) -> pathlib.Path:
    # Fast path when building on CI
    if "BUILD_WORKSPACE_DIRECTORY" in env:
        return pathlib.Path(env["BUILD_WORKSPACE_DIRECTORY"])
    root = pathlib.Path(__file__).resolve().parent
    while not any((root / x).exists() for x in _REPO_MARKERS):
        parent = root.parent
        if parent == root:
            raise FileNotFoundError(
                f"Could not find repo root containing any marker {_REPO_MARKERS} from {__file__}"
            )
        root = parent
    return root


def _getuser() -> str:
    """Gets the logged in user.

    Fallback to os.getlogin() since getpass on Windows may raise an exception.
    """
    try:
        return getpass.getuser()
    except (OSError, KeyError):
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
        logger.error("Failed to modify key, error: %s.", err)

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
            logger.error("Failed to remove key, error: %s.", err)


async def _read_stream(
    stream: asyncio.StreamReader, log_fn: Callable[[str], None] | None
) -> str:
    """Asynchronously reads lines from a stream and either logs (if log_fn is not None) or returns them."""
    output = []
    try:
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace")
            if not log_fn:
                output.append(line)
            elif line:
                log_fn(line.rstrip())
    except Exception:
        logger.exception("Stream closed unexpectedly")
        if output:
            logger.warning("--- PARTIAL OUTPUT CAPTURED BEFORE FAILURE ---")
            for line in output:
                logger.warning(line.rstrip())
            logger.warning("--------------------------------------------")
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

    build_target: str | None
    host_platform: str
    target_platform: str
    repo_root: pathlib.Path
    user: str
    tmp_dir: pathlib.Path | None

    _start_time: float
    _cmd_env: dict[str, str]

    def __init__(
        self,
        args: argparse.Namespace,
        env: dict[str, str] | None = None,
        repo_root: pathlib.Path | None = None,
        user: str | None = None,
    ):
        """Initializes the BazelEnvironment.

        Args:
            args: Parsed command-line arguments.
            env: The environment variables. Defaults to os.environ.
            repo_root: The root of the repository. Defaults to finding it.
            user: The current user. Defaults to the logged-in user.
        """
        env_dict = os.environ if env is None else env
        self.build_target = env_dict.get("BUILD_TARGET_NAME")
        self.host_platform = platform.system().lower()
        self.target_platform = (
            args.target if hasattr(args, "target") else default_target()
        )
        self.repo_root = find_repo_root(env_dict) if repo_root is None else repo_root
        self.user = _getuser() if user is None else user
        self.tmp_dir = (
            pathlib.Path(env_dict["TMPDIR"]) if env_dict.get("TMPDIR") else None
        )

        self._start_time = time.time()
        self._cmd_env = env_dict.copy()
        self._cmd_env["PYTHONUNBUFFERED"] = "1"
        for env_secret_key in ("EMULATOR_SYMBOL_SERVER_KEY", "GEMINI_API_KEY"):
            val = self._cmd_env.get(env_secret_key)
            if isinstance(val, str):
                register_secret(val)
        self.python = self._resolve_python()

    def _resolve_python(self) -> pathlib.Path:
        subpath = (
            pathlib.Path("python.exe")
            if self.is_windows()
            else (pathlib.Path("bin") / "python3")
        )
        python_candidate = (
            self.repo_root
            / "prebuilts"
            / "python"
            / f"{self.host_platform}-x86"
            / subpath
        )
        if python_candidate.exists():
            return python_candidate
        return pathlib.Path(sys.executable)

    def get_env(self):
        """Gets the OS environment that should be used when running a program."""
        return self._cmd_env

    def __enter__(self):
        """Configure platform specific settings if needed."""
        self._start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        end_time = time.time()
        execution_time = end_time - self._start_time

        # Format the execution time nicely
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(execution_time))
        logger.info("Completed build in %s", formatted_time)

    def is_macos(self):
        return False

    def is_windows(self):
        return False

    def is_linux(self):
        return not self.is_macos() and not self.is_windows()

    def is_cross_compile(self) -> bool:
        """Determines whether the target platform is cross-compiled from the host."""
        native_target = _SYSTEM_TO_TARGET.get(self.host_platform)
        return bool(
            native_target
            and self.target_platform != native_target
            and "trusty" not in self.target_platform
        )

    def get_host_memory_metrics(self):
        """Returns host memory and swap metrics."""
        return host_resources.get_host_memory_metrics(self)

    def is_memory_constrained(self) -> bool:
        """Determines whether the host environment is memory-constrained."""
        return host_resources.is_memory_constrained(self)

    def mask_command(self, cmd: Iterable[Any]) -> list[str]:
        """Returns a stringified copy of cmd with all API keys and secrets redacted."""
        secrets = [
            getattr(self, "crashpad_symbol_server_key", None),
            getattr(self, "gemini_api_key", None),
            self._cmd_env.get("EMULATOR_SYMBOL_SERVER_KEY"),
            self._cmd_env.get("GEMINI_API_KEY"),
        ]
        str_secrets = [s for s in secrets if isinstance(s, str) and s]
        for s in str_secrets:
            register_secret(s)
        return mask_command(cmd, extra_secrets=str_secrets)

    async def _run_async(
        self,
        cmd: list[str],
        *,
        capture_output: bool = False,
        env: dict[str, str] | None = None,
        cwd: pathlib.Path | str | None = None,
        throw_on_failure: bool = True,
        timeout: float | None = 3600,
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
        log_cmd_str = self.mask_command(cmd_str)
        logger.info("%s $> %s", cwd, " ".join(log_cmd_str))

        proc = await asyncio.create_subprocess_exec(
            *cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        stdout: str | None = None
        stderr: str | None = None

        async def _execute_with_stream_capture():
            nonlocal stdout, stderr
            async with asyncio.TaskGroup() as tg:
                stdout_task = tg.create_task(
                    _read_stream(proc.stdout, None if capture_output else logger.info)
                )
                stderr_task = tg.create_task(
                    _read_stream(proc.stderr, None if capture_output else logger.error)
                )
                tg.create_task(proc.wait())
            stdout = stdout_task.result() or None
            stderr = stderr_task.result() or None

        try:
            await asyncio.wait_for(_execute_with_stream_capture(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            logger.error("Command timed out after %s seconds, terminating.", timeout)
            raise subprocess.TimeoutExpired(log_cmd_str, timeout)
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except (
                    asyncio.TimeoutError,
                    asyncio.CancelledError,
                    Exception,
                ):  # noqa: BLE001
                    logger.error("Command did not terminate after 10s, killing.")
                    proc.kill()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except Exception as wait_err:  # noqa: BLE001
                        logger.debug("Error waiting for killed process: %s", wait_err)

        result = subprocess.CompletedProcess(
            args=log_cmd_str, returncode=proc.returncode, stdout=stdout, stderr=stderr
        )

        if proc.returncode != 0 and throw_on_failure:
            msg = [
                f"Failed to run {' '.join(log_cmd_str)}, exit code: {proc.returncode}"
            ]
            raise CommandFailedException("\n".join(msg), result)

        return result

    def run(
        self,
        cmd: list[str],
        *,
        capture_output: bool = False,
        env: dict[str, str] | None = None,
        cwd: pathlib.Path | str | None = None,
        throw_on_failure: bool = True,
        timeout: float | None = 3600,
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
    is_release: bool
    dist_dir: pathlib.Path
    crashpad_symbol_server_key: str
    crashpad_server: str

    CRASHPAD_PROD = "https://prod-crashsymbolcollector-pa.googleapis.com"
    CRASHPAD_STAGING = "https://staging-crashsymbolcollector-pa.googleapis.com"

    def __init__(
        self,
        args: argparse.Namespace,
        env: dict[str, str] | None = None,
        repo_root: pathlib.Path | None = None,
        user: str | None = None,
    ):
        """Initializes the BuildEnvironment.

        Args:
            args: Parsed command-line arguments.
            env: The environment variables. Defaults to os.environ.
            repo_root: The root of the repository. Defaults to finding it.
            user: The current user. Defaults to the logged-in user.
        """
        super().__init__(args, env=env, repo_root=repo_root, user=user)
        self.build_id = args.build_id if hasattr(args, "build_id") else "PNONE"
        self.build_target = self._cmd_env.get("BUILD_TARGET_NAME")
        self.is_presubmit = self.build_id.startswith("P")
        self.is_release = "release" in getattr(args, "config", [])
        self.dist_dir = (
            pathlib.Path(args.dist)
            if hasattr(args, "dist") and args.dist
            else pathlib.Path("PNONE")
        )

        self.crashpad_symbol_server_key = self._cmd_env.get(
            "EMULATOR_SYMBOL_SERVER_KEY"
        )
        self.crashpad_server = self.CRASHPAD_PROD
        if self.is_presubmit:
            self.crashpad_server = self.CRASHPAD_STAGING

        if not self.crashpad_symbol_server_key:
            key_path = pathlib.Path.home() / ".emulator_symbol_server_key"
            try:
                with open(key_path, "r") as f:
                    self.crashpad_symbol_server_key = f.read().strip()
            except FileNotFoundError:
                log_fn = logger.debug if self.is_presubmit else logger.info
                log_fn(
                    "Symbol server key file not found at %s (skipped if not uploading symbols)",
                    key_path,
                )
        if isinstance(self.crashpad_symbol_server_key, str):
            register_secret(self.crashpad_symbol_server_key)

        self.gemini_api_key = None
        try:
            try:
                from gemini.auth import GeminiAuthenticator
            except ImportError:
                from gemini_auth import GeminiAuthenticator
            auth = GeminiAuthenticator()
            creds = auth.get_credentials("gemini-flash-latest")
            self.gemini_api_key = creds.get("api_key")
            if isinstance(self.gemini_api_key, str):
                register_secret(self.gemini_api_key)
        except (
            Exception
        ) as e:  # noqa: BLE001  # Fallback gracefully if Gemini credentials cannot be resolved
            logger.warning(
                "Could not obtain Gemini API key: %s", redact_secrets(str(e))
            )

    def __enter__(self):
        self.dist_dir.mkdir(exist_ok=True, parents=True)
        mode = "presubmit" if self.is_presubmit else "postsubmit"
        metrics = self.get_host_memory_metrics()
        total_bytes = getattr(metrics, "total_bytes", 0)
        ram_str = (
            f"{metrics.total_gb:.1f} GiB"
            if isinstance(total_bytes, (int, float)) and total_bytes > 0
            else "unknown"
        )
        logger.info(
            "Build config: build_id=%s (%s), target=%s, host=%s (RAM=%s, constrained=%s), dist=%s",
            self.build_id,
            mode,
            self.target_platform,
            self.host_platform,
            ram_str,
            self.is_memory_constrained(),
            self.dist_dir,
        )
        return super().__enter__()


class LinuxBazelEnvironment(BazelEnvironment):
    """Linux-specific Bazel environment configuration."""

    def is_linux(self):
        return True


class WindowsBazelEnvironment(BazelEnvironment):
    """Windows-specific Bazel environment configuration."""

    def __enter__(self):
        disable_debug_policy()
        return super().__enter__()

    def is_windows(self):
        return True


class MacOSBazelEnvironment(BazelEnvironment):
    """macOS-specific Bazel environment configuration."""

    def is_macos(self):
        return True


class WindowsBuildEnvironment(BuildEnvironment):
    """Environment for Windows builds, configuring Windows debug policies."""

    def __enter__(self):
        disable_debug_policy()
        return super().__enter__()

    def is_windows(self):
        return True


class LinuxBuildEnvironment(BuildEnvironment):
    """Environment for Linux builds, configuring prebuilt Python."""

    def is_linux(self):
        return True


class MacOSBuildEnvironment(BuildEnvironment):
    """Environment for macOS builds, configuring Xcode and prebuilt Python."""

    def __enter__(self):
        if "DEVELOPER_DIR" not in self._cmd_env:
            try:
                xcode_path = subprocess.check_output(
                    ["xcode-select", "-p"], text=True
                ).strip()
                self._cmd_env["DEVELOPER_DIR"] = xcode_path
            except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
                logger.warning(
                    "Could not determine DEVELOPER_DIR via xcode-select: %s", e
                )
        return super().__enter__()

    def is_macos(self):
        return True


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


def create_bazel_environment(args: argparse.Namespace) -> BazelEnvironment:
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
