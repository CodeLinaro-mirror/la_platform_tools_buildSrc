# Copyright 2024 - The Android Open Source Project
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

import enum
import functools
import pathlib
import subprocess
import types
from typing import Iterable, List, Mapping, Tuple, Type, TypeVar, Union
import build_environment

_PLATFORM_TARGETS_BY_NAME = {
    "linux_x64": "@//build/bazel/platforms:linux_x64",
    "mac_aarch64": "@//build/bazel/platforms:macos_aarch64",
    "mac_x64": "@//build/bazel/platforms:macos_x64",
    "windows_x64": "@//build/bazel/platforms:windows_x64",
}

ExitCodeType = TypeVar("ExitCodeType", bound="BaseExitCode")


class _ExitCode(enum.IntEnum):
    @classmethod
    def try_convert(cls: Type[ExitCodeType], code: int) -> Union[ExitCodeType, int]:
        try:
            return cls(code)
        except ValueError:
            return code


class BaseExitCode(_ExitCode):
    SUCCESS = 0
    BAD_COMMANDLINE = 2
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45


class BuildExitCode(_ExitCode):
    SUCCESS = 0
    BAD_COMMANDLINE = 2
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45

    BUILD_FAILED = 1
    TESTS_FAILED = 3
    TESTS_NOT_FOUND = 4


class QueryExitCode(_ExitCode):
    SUCCESS = 0
    BAD_COMMANDLINE = 2
    INTERRUPTED = 8
    SERVER_LOCKED = 9
    REMOTE_ENVIRONMENT_ISSUE = 32
    OUT_OF_MEMORY = 33
    LOCAL_ENVIRONMENT_ISSUE = 36
    INTERNAL_ERROR = 37
    BES_RETRIABLE_ERROR = 38
    REMOTE_CACHE_BLOB_MISSING = 39
    BES_ERROR = 45

    QUERY_PARTIAL_SUCCESS = 3
    QUERY_COMMAND_FAILED = 7


def _bazel_error_msg(
    cmd: List[str], returncode: Union[int, _ExitCode], stderr: str = None
) -> str:
    if isinstance(returncode, _ExitCode):
        returncode = returncode.name
    msg = f"Bazel command {' '.join(cmd)} returned {returncode}"
    if stderr:
        msg += f"\nSTDERR:\n{stderr}"
    return msg


class BazelCmd:
    """A wrapper that constructs bazel commands and handle results for CI.

    Typical usage::

        bzl = BazelCmd(env, startup_options=options).with_build_flags(common_flags)
        bzl.test(test_targets + build_targets, invocation_flags=test_specific_flags)
        bzl.build(targets, invocation_flags=build_specific_flags)
        artifacts = bzl.query_artifacts(targets)
        # handle artifacts

    Changing ``startup_options`` without changing ``--output_base`` at the same
    time causes bazel server to restart and lose of the analysis cache. This
    should be prevented if at all possible.

    All build-related flags, if possible, should be passed as
    ``with_build_flags(common_flags)`` so they are memorized to avoid discarding
    the analysis cache. If your build flags are changed on purpose, pass
    ``allow_analysis_cache_discard=True`` on the build/test/cquery invocation
    with the change - or the build will fail otherwise.
    """

    _env: build_environment.BuildEnvironment
    _path: pathlib.Path
    _startup_options: Tuple[str, ...]
    _build_flags: Tuple[str, ...]
    _capture_output: bool

    def __init__(
        self,
        build_env: build_environment.BuildEnvironment,
        *,
        capture_output: bool = False,
        startup_options: Iterable[str] = (),
    ) -> None:
        """Initializes Bazel command.

        Args:
            build_env: The build environment.
            capture_output: Whether or not build, test and run commands should capture
                outputs. Queries always capture output.
            startup_options: Startup options for the bazel command.
        """
        self._env = build_env
        self._startup_options = tuple(startup_options)
        self._build_flags = ()
        system = f"{build_env.host_platform}-x86_64"
        self._path = build_env.repo_root / "prebuilts" / "bazel" / system / "bazel"
        self._capture_output = capture_output

    def with_build_flags(self, flags: Iterable[str]) -> "BazelCmd":
        """Creates a new ``BazelCmd`` instance with build flags attached.

        This is roughly equivalent to passing the same flags as
        ``invocation_flags`` to all the invocations, and helps to avoid
        discarding the analysis cache.

        Args:
            flags: The flags to attach to the instance.

        Returns:
            A new ``BazelCmd`` object.
        """
        instance = BazelCmd(
            self._env,
            capture_output=self._capture_output,
            startup_options=self._startup_options,
        )
        instance._build_flags = tuple(flags)
        return instance

    def _build_cmd(
        self,
        verb: str,
        queries: Iterable[str] = (),
        extra_flags: Iterable[str] = (),
        allow_analysis_cache_discard: bool = False,
    ) -> List[str]:
        cmd = [str(self._path)]
        cmd.extend(self._startup_options)
        cmd.append(verb)
        cmd.extend(self._build_flags)
        cmd.append(
            "--platforms={}".format(
                _PLATFORM_TARGETS_BY_NAME[self._env.target_platform]
            )
        )
        cmd.extend(extra_flags)
        if not allow_analysis_cache_discard:
            cmd.append("--noallow_analysis_cache_discard")
        if queries:
            cmd.append("--")
            cmd.extend(queries)
        return cmd

    @functools.cached_property
    def info(self) -> Mapping[str, str]:
        """Returns the parsed output of ``bazel info``.

        Uses the stored build flags and fails if analysis cache gets discarded.
        """
        cmd = self._build_cmd("info")
        result = self._env.run(
            cmd, throw_on_failure=False, capture_output=True, timeout=300
        )
        if result.returncode != BaseExitCode.SUCCESS:
            raise build_environment.CommandFailedException(
                _bazel_error_msg(
                    cmd, BaseExitCode.try_convert(result.returncode), result.stderr
                ),
                result,
            )
        info = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
        return types.MappingProxyType(info)

    def test(
        self,
        targets: Iterable[str],
        *,
        invocation_flags: Iterable[str] = (),
        allow_analysis_cache_discard: bool = False,
        allow_no_test: bool = False,
        timeout: Union[float, None] = 3600,
    ) -> subprocess.CompletedProcess:
        """Runs ``bazel test`` and returns the result.

        Args:
            targets: The bazel targets. You can pass non-test targets here and
                bazel will simply build them
            invocation_flags: Per-invocation flags.
            allow_analysis_cache_discard: Do not fail the build if analysis
                cache is being discarded.
            allow_no_test: Do not raise exception if bazel reports no tests were
                run.
            timeout: Timeout for the entire process.

        Returns:
            A ``subprocess.CompletedProcess`` object. When capture_output == False,
            it contains only the commandline and the return code.
        """
        cmd = self._build_cmd(
            "test", targets, invocation_flags, allow_analysis_cache_discard
        )
        result = self._env.run(
            cmd,
            capture_output=self._capture_output,
            throw_on_failure=False,
            timeout=timeout,
        )
        normal_return_codes = [BuildExitCode.SUCCESS, BuildExitCode.TESTS_FAILED]
        if allow_no_test:
            normal_return_codes.append(BuildExitCode.TESTS_NOT_FOUND)
        if result.returncode not in normal_return_codes:
            raise build_environment.CommandFailedException(
                _bazel_error_msg(cmd, BuildExitCode.try_convert(result.returncode)),
                result,
            )
        return result

    def build(
        self,
        targets: Iterable[str],
        *,
        invocation_flags: Iterable[str] = (),
        allow_analysis_cache_discard: bool = False,
        timeout: Union[float, None] = 3600,
    ) -> subprocess.CompletedProcess:
        """Runs ``bazel build`` and returns the result..

        Args:
            targets: The bazel targets to build.
            invocation_flags: Per-invocation flags
            allow_analysis_cache_discard: Do not fail the build if analysis
                cache is being discarded.
            timeout: Timeout for the entire process.

        Returns:
            A ``subprocess.CompletedProcess`` object. When capture_output == False,
            it contains only the commandline and the return code.
        """
        cmd = self._build_cmd(
            "build", targets, invocation_flags, allow_analysis_cache_discard
        )
        result = self._env.run(
            cmd,
            capture_output=self._capture_output,
            throw_on_failure=False,
            timeout=timeout,
        )
        if result.returncode != BuildExitCode.SUCCESS:
            raise build_environment.CommandFailedException(
                _bazel_error_msg(cmd, BuildExitCode.try_convert(result.returncode)),
                result,
            )
        return result

    def run(
        self,
        target: str,
        *,
        invocation_flags: Iterable[str] = (),
        params: Iterable[str] = (),
        allow_analysis_cache_discard: bool = False,
        timeout: Union[float, None] = 3600,
    ) -> subprocess.CompletedProcess:
        """Runs ``bazel run`` and returns the result..

        Args:
            target: The bazel target to run.
            invocation_flags: Per-invocation flags
            allow_analysis_cache_discard: Do not fail the build if analysis
                cache is being discarded.
            timeout: Timeout for the entire process.
            params: The set of params to pass to the target.

        Returns:
            A ``subprocess.CompletedProcess`` object. When capture_output == False,
            it contains only the commandline and the return code.
        """
        flags = [target] + params
        cmd = self._build_cmd(
            "run", flags, invocation_flags, allow_analysis_cache_discard
        )
        result = self._env.run(
            cmd,
            capture_output=self._capture_output,
            throw_on_failure=True,
            timeout=timeout,
        )
        return result

    def cquery(
        self,
        query: str,
        *,
        invocation_flags: Iterable[str] = (),
        allow_analysis_cache_discard: bool = False,
        keep_going: bool = False,
        timeout: Union[float, None] = 300,
    ) -> subprocess.CompletedProcess:
        """Runs ``bazel cquery`` and returns the result.

        Args:
            query: The query string.
            invocation_flags: Per-invocation flags
            allow_analysis_cache_discard: Do not fail the build if analysis
                cache is being discarded.
            keep_going: Allow bazel to continue on errors and retrieve partial
                results.
            timeout: Timeout for the entire process.

        Returns:
            A ``subprocess.CompletedProcess`` object. It contains the
            commandline, the return code, stderr and stdout.
        """
        if keep_going:
            invocation_flags = list(invocation_flags)
            invocation_flags.append("--keep_going")
        cmd = self._build_cmd(
            "cquery", [query], invocation_flags, allow_analysis_cache_discard
        )
        result = self._env.run(
            cmd, throw_on_failure=False, capture_output=True, timeout=timeout
        )
        normal_return_codes = [QueryExitCode.SUCCESS]
        if keep_going:
            normal_return_codes.append(QueryExitCode.QUERY_PARTIAL_SUCCESS)
        if result.returncode not in normal_return_codes:
            raise build_environment.CommandFailedException(
                _bazel_error_msg(
                    cmd, QueryExitCode.try_convert(result.returncode), result.stderr
                ),
                result,
            )
        return result

    def query_artifacts(
        self, targets: Iterable[str], *, timeout: Union[float, None] = 300
    ) -> List[pathlib.Path]:
        """Queries the output files of the passed targets.

        This runs a ``cquery`` and an ``info`` under the hood, and passes the
        build flags stored in the instance.

        Args:
            targets: The targets to query for.
            timeout: Timeout for the entire process.
        """
        query = " + ".join(quote(t) for t in targets)
        result = self.cquery(
            query, invocation_flags=["--output=files"], timeout=timeout
        )
        exec_root = pathlib.Path(self.info["execution_root"])
        artifacts = []
        for line in result.stdout.splitlines():
            line = line.strip()
            artifacts.append(exec_root / line)
        return artifacts

    def mod(
        self, args: Iterable[str], *, timeout: Union[float, None] = 300
    ) -> subprocess.CompletedProcess:
        """Runs bazel mod command.

        Just a plain "bazel mod" followed by the args. The start-up options and the
        build flags are still honored.
        """
        cmd = (
            [self._path]
            + list(self._startup_options)
            + ["mod"]
            + list(self._build_flags)
            + list(args)
        )
        result = self._env.run(
            cmd,
            capture_output=self._capture_output,
            throw_on_failure=True,
            timeout=timeout,
        )
        return result


def quote(s: str) -> str:
    return f"'{s}'"
