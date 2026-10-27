# Copyright 2026 - The Android Open Source Project
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
import json
import logging
import pathlib
import platform
import shutil
import stat
from collections.abc import Iterable

logger = logging.getLogger(__name__)

try:
    from engine import bazel
    from host import build_environment
    from services.change_info import ChangeInfo
except ImportError:
    import bazel
    import build_environment
    from change_info import ChangeInfo


def safe_copy_file(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """Copies a file to dest, safely handling existing read-only destination files.

    If the target destination file already exists and is read-only (common with
    Bazel artifacts and test logs), it is unlinked before copying. Write
    permission is also ensured on the copied destination file so future
    re-runs or downstream tools do not fail with PermissionError.
    """
    target = dest / src.name if dest.is_dir() else dest
    if target.is_file() or target.is_symlink():
        try:
            target.unlink()
        except OSError:
            try:
                target.chmod(target.stat().st_mode | stat.S_IWUSR)
                target.unlink()
            except OSError:
                pass

    try:
        result = shutil.copy2(src, dest)
    except PermissionError:
        # Fallback if unlink was not possible initially or due to race conditions
        if target.exists():
            try:
                target.chmod(target.stat().st_mode | stat.S_IWUSR)
                target.unlink()
            except OSError:
                pass
        result = shutil.copy2(src, dest)

    target_path = pathlib.Path(result)
    try:
        target_path.chmod(target_path.stat().st_mode | stat.S_IWUSR)
    except OSError:
        pass
    return target_path


def copy_all(
    srcs: Iterable[pathlib.Path], dest: pathlib.Path, not_found_ok: bool = False
):
    """Copies all source files to the destination directory."""
    for f in srcs:
        try:
            f_dest = safe_copy_file(f, dest)
            logger.info("Copied '%s' to '%s'", f, f_dest)
        except FileNotFoundError:
            if not_found_ok:
                continue
            raise


class BuildContext:
    """Encapsulates shared state, directories, and logging helpers across pipeline stages."""

    def __init__(
        self,
        args: argparse.Namespace,
        env: build_environment.BuildEnvironment,
        startup_options: list[str] | None = None,
        build_options: list[str] | None = None,
    ):
        self.args = args
        self.env = env
        self.startup_options = list(startup_options or [])
        self.build_options = list(build_options or [])
        self.dist_dir = env.dist_dir
        self.logs_dir = self.dist_dir / "logs"
        self.bazel_logs_dir = self.logs_dir / "bazel"
        self.testlogs_dest_dir = self.logs_dir / "testlogs"

        self.bazel_logs_dir.mkdir(parents=True, exist_ok=True)

        self.change_info: ChangeInfo | None = (
            ChangeInfo(args.change_info) if getattr(args, "change_info", None) else None
        )
        self.active_bzl: bazel.BazelCmd | None = None
        self._clang_tidy_cache: tuple[tuple[object, ...], list[str]] | None = None

    def get_clang_tidy_build_flags(self) -> list[str]:
        """Returns Starlark clang-tidy build flags when goldfish C/C++ files are modified."""
        if (
            not self.change_info
            or getattr(self.args, "aosp", False)
            or self.env.is_windows()
            or not self.env.is_release
        ):
            return []

        cache_key = (
            id(self.change_info),
            bool(self.env.is_windows()),
            bool(self.env.is_release),
        )
        if self._clang_tidy_cache and self._clang_tidy_cache[0] == cache_key:
            return list(self._clang_tidy_cache[1])

        tidy_filters = self.change_info.get_clang_tidy_line_filter_json_by_project(
            "hardware/generic/goldfish", self.env
        )
        flags: list[str] = []
        if tidy_filters:
            logger.info("Enabling clang-tidy targets for modified C/C++ lines")
            flags.extend(
                [
                    "--@goldfish_build//:clang_tidy_enabled=true",
                    f"--@goldfish_build//:clang_tidy_line_filter={tidy_filters}",
                ]
            )
            try:
                for filter_obj in json.loads(tidy_filters):
                    if "name" in filter_obj:
                        flags.append(
                            f"--@goldfish_build//:clang_tidy_check_files={filter_obj['name']}"
                        )
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Could not parse clang-tidy filters: %s", e)

        self._clang_tidy_cache = (cache_key, flags)
        return list(flags)

    def copy_artifacts(self, artifacts: Iterable[pathlib.Path]) -> None:
        """Copies built artifacts into the dist directory."""
        copy_all(artifacts, self.dist_dir)

    def copy_bazel_logs(self, bzl: bazel.BazelCmd | None = None) -> None:
        """Copies Bazel server logs, worker logs, and testlogs into dist_dir/logs."""
        target_bzl = bzl or self.active_bzl
        if not target_bzl:
            return

        try:
            self.bazel_logs_dir.mkdir(parents=True, exist_ok=True)
            output_base = pathlib.Path(target_bzl.info["output_base"])

            logs = [
                pathlib.Path(target_bzl.info["server_log"]),
                output_base / "server" / "jvm.out",
            ]
            worker_dir = output_base / "bazel-workers"
            if worker_dir.is_dir():
                logs.extend(worker_dir.glob("*.log"))

            copy_all(logs, self.bazel_logs_dir, not_found_ok=True)

            testlogs_dirs: list[pathlib.Path] = []
            if "bazel-testlogs" in target_bzl.info:
                primary_testlogs = pathlib.Path(target_bzl.info["bazel-testlogs"])
                if primary_testlogs.is_dir():
                    testlogs_dirs.append(primary_testlogs)

            if "output_path" in target_bzl.info:
                output_path = pathlib.Path(target_bzl.info["output_path"])
                if output_path.is_dir():
                    for candidate in sorted(output_path.glob("*/testlogs")):
                        if candidate.is_dir() and candidate not in testlogs_dirs:
                            testlogs_dirs.append(candidate)
                    coverage_report = (
                        output_path / "_coverage" / "_coverage_report.dat"
                    )
                    if coverage_report.is_file():
                        self.dist_dir.mkdir(parents=True, exist_ok=True)
                        dest_report = safe_copy_file(
                            coverage_report,
                            self.dist_dir / "coverage_report.dat",
                        )
                        logger.info(
                            "Copied combined coverage report '%s' to '%s'",
                            coverage_report,
                            dest_report,
                        )

            if testlogs_dirs:
                self.testlogs_dest_dir.mkdir(parents=True, exist_ok=True)
                copied_testlogs = 0
                for testlogs_dir in testlogs_dirs:
                    for ext in ("*.log", "*.xml", "coverage.dat"):
                        for f in testlogs_dir.rglob(ext):
                            rel = f.relative_to(testlogs_dir)
                            dest = self.testlogs_dest_dir / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                safe_copy_file(f, dest)
                                copied_testlogs += 1
                            except FileNotFoundError:
                                continue
                            except (OSError, shutil.Error) as file_err:
                                logger.warning(
                                    "Failed to copy testlog %s to %s: %s",
                                    f,
                                    dest,
                                    file_err,
                                )
                if copied_testlogs > 0:
                    logger.info(
                        "Copied %d test log/XML/coverage file(s) to '%s'",
                        copied_testlogs,
                        self.testlogs_dest_dir,
                    )
        except (OSError, shutil.Error, KeyError) as e:
            logger.warning("Failed to copy bazel logs: %s", e)

    def should_run_meson_generator(self) -> bool:
        """Determines if the QEMU/Meson AMC build generator check should run."""
        # Don't try to run AMC for debug, TSAN or ASAN builds.
        if not self.env.is_release:
            return False

        # Always run in postsubmit.
        if not self.env.is_presubmit:
            logger.info("Running generator because not in presubmit")
            return True

        if self.change_info:
            if self.change_info.get_commits_by_project("platform/external/qemu"):
                logger.info("Running generator because change affects qemu")
                return True
            if self.change_info.get_commits_by_project("trusty/external/qemu-meson"):
                logger.info("Running generator because change affects meson")
                return True
            if self.change_info.get_commits_by_project("platform/hardware/google/aemu"):
                logger.info("Running generator because change affects aemu")
                return True

        if platform.system().lower() == "linux":
            logger.info("Running generator because host is Linux")
            return True

        return False
