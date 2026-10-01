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

import json
import logging
import os
import pathlib

logger = logging.getLogger(__name__)

try:
    from engine import bazel
    from engine.invocation import BazelInvocationBuilder
    from host import build_environment, host_resources
    from pipeline.context import BuildContext
    from pipeline.stage import Stage
    from services import sym_upload
except ImportError:
    import bazel
    import build_environment
    import host_resources
    import sym_upload
    from context import BuildContext
    from invocation import BazelInvocationBuilder
    from stage import Stage


try:
    from unittest.mock import MagicMock
except ImportError:
    MagicMock = None


def _is_env_memory_constrained(env: build_environment.BuildEnvironment) -> bool:
    """Checks if the environment is memory-constrained, handling unconfigured mocks safely."""
    if hasattr(env, "is_memory_constrained"):
        res = env.is_memory_constrained()
        if MagicMock is not None and isinstance(res, MagicMock):
            return bool(env.is_macos())
        return bool(res)
    return bool(env.is_macos())


def _log_host_memory_status(env: build_environment.BuildEnvironment, label: str = ""):
    """Logs host memory and swap metrics for telemetry and diagnostics."""
    host_resources.log_host_memory_status(env, label)


log_host_memory_status = _log_host_memory_status


def reclaim_and_stabilize_resources(
    env: build_environment.BuildEnvironment,
    *bzl_instances: bazel.BazelCmd | None,
):
    """Reclaims system memory and logs host state before heavy integration tests."""
    logger.info("Reclaiming resources before ETS integration tests...")
    _log_host_memory_status(env, "before shutdown")

    for bzl in bzl_instances:
        if bzl:
            try:
                bzl.shutdown()
            except Exception as e:  # noqa: BLE001  # Resource cleanup must not abort pipeline
                logger.warning("Failed to shutdown Bazel server: %s", e)

    try:
        if hasattr(os, "sync"):
            os.sync()
    except OSError as e:
        logger.warning("os.sync failed: %s", e)

    _log_host_memory_status(env, "after shutdown")


class ClangTidyStage(Stage):
    """Executes a fast-fail standalone Bazel pass exclusively running clang-tidy checks."""

    def __init__(self):
        self._tidy_filters: str | None = None
        self._bzl_tidy: bazel.BazelCmd | None = None

    @property
    def name(self) -> str:
        return "Clang-Tidy Pre-Pass"

    def should_run(self, ctx: BuildContext) -> bool:
        if not ctx.change_info:
            return False
        self._tidy_filters = ctx.change_info.get_clang_tidy_line_filter_json_by_project(
            "hardware/generic/goldfish", ctx.env
        )
        return bool(self._tidy_filters)

    def _find_tidy_targets(self, ctx: BuildContext) -> list[str]:
        try:
            filters = json.loads(self._tidy_filters or "")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Could not parse clang-tidy filters: %s", e)
            return ["@goldfish//..."]

        goldfish_root = ctx.env.repo_root / "hardware" / "generic" / "goldfish"
        targets = set()
        for filter_obj in filters:
            rel_path = filter_obj.get("name")
            if not rel_path:
                continue
            file_path = goldfish_root / rel_path
            curr = file_path.parent
            while curr != goldfish_root and curr != curr.parent:
                if (curr / "BUILD.bazel").exists() or (curr / "BUILD").exists():
                    pkg_rel = curr.relative_to(goldfish_root).as_posix()
                    targets.add(f"@goldfish//{pkg_rel}:all")
                    break
                curr = curr.parent

        if targets:
            logger.info("Scoped clang-tidy pre-pass to targets: %s", sorted(targets))
            return sorted(targets)
        return ["@goldfish//..."]

    def execute(self, ctx: BuildContext) -> None:
        logger.info("Enabling clang-tidy targets for modified C/C++ lines")
        tidy_build_options = list(ctx.build_options) + [
            "--@goldfish_build//:clang_tidy_enabled=true",
            f"--@goldfish_build//:clang_tidy_line_filter={self._tidy_filters}",
        ]
        for filter_obj in json.loads(self._tidy_filters or "[]"):
            tidy_build_options.append(
                f"--@goldfish_build//:clang_tidy_check_files={filter_obj['name']}"
            )

        targets = self._find_tidy_targets(ctx)
        logger.info(
            "Running fast-fail pre-pass exclusively for clang-tidy checks on %s...",
            targets,
        )
        self._bzl_tidy = bazel.BazelCmd(
            ctx.env, startup_options=ctx.startup_options
        ).with_build_flags(tidy_build_options)
        ctx.active_bzl = self._bzl_tidy

        res = self._bzl_tidy.test(
            targets,
            invocation_flags=[
                "--build_tests_only",
                "--test_tag_filters=tidy-test",
                "--test_output=errors",
                "--modify_execution_info=ClangTidy.*=+no-remote",
            ],
            allow_analysis_cache_discard=True,
            allow_no_test=True,
            fail_on_test_failure=False,
            timeout=600,
        )
        if res.returncode == bazel.BuildExitCode.TESTS_NOT_FOUND:
            logger.info(
                "No clang-tidy tests found for %s, skipping pre-pass.", targets
            )
            return
        if res.returncode != 0:
            raise build_environment.CommandFailedException(
                "Clang-tidy pre-pass encountered violations.", res
            )

    def on_failure(self, ctx: BuildContext, exc: Exception) -> None:
        if not self._bzl_tidy:
            return
        try:
            if "bazel-testlogs" not in self._bzl_tidy.info:
                return
            testlogs_dir = pathlib.Path(self._bzl_tidy.info["bazel-testlogs"])
            if not testlogs_dir.is_dir():
                return

            for log_val in testlogs_dir.rglob("test.log"):
                content = log_val.read_text(encoding="utf-8", errors="replace")
                if (
                    "Clang-tidy found issues" in content
                    or "clang-tidy crashed" in content
                ):
                    try:
                        rel_path = log_val.relative_to(testlogs_dir)
                    except ValueError:
                        rel_path = log_val
                    logger.error(
                        "\n"
                        + "=" * 80
                        + f"\n[LINT FAILURE] Report from: {rel_path}\n"
                        + "=" * 80
                        + f"\n{content}\n"
                        + "=" * 80
                    )
        except Exception as e:  # noqa: BLE001  # Telemetry extraction must not mask the primary failure
            logger.warning("Failed to extract clang-tidy logs: %s", e)


class AospStage(Stage):
    """Executes the minimal AOSP build pipeline."""

    @property
    def name(self) -> str:
        return "AOSP Minimal Release"

    def should_run(self, ctx: BuildContext) -> bool:
        return getattr(ctx.args, "aosp", False)

    def execute(self, ctx: BuildContext) -> None:
        aosp_build_options = list(ctx.build_options)
        if "--config=aosp" not in aosp_build_options:
            aosp_build_options.append("--config=aosp")
        if "--lockfile_mode=off" not in aosp_build_options:
            aosp_build_options.append("--lockfile_mode=off")

        inv = (
            BazelInvocationBuilder("build", ctx.env, ctx.startup_options)
            .with_targets(["@goldfish//emulator:release"])
            .with_common_flags(aosp_build_options)
            .with_profile(ctx.bazel_logs_dir / "aosp_build.profile.gz")
            .build_invocation()
        )

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        logger.info("Starting AOSP build for target @goldfish//emulator:release...")
        bzl.build(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=inv.timeout,
        )

        artifacts = bzl.query_artifacts(
            ["@goldfish//emulator:release"], allow_analysis_cache_discard=True
        )
        ctx.copy_artifacts(artifacts)
        logger.info("AOSP build complete. Artifacts placed in %s", ctx.dist_dir)


class ReleasePackageStage(Stage):
    """Builds release binaries and runs fast boot tests under --config=release (ThinLTO)."""

    def __init__(self):
        self.release_targets: list[str] = []

    @property
    def name(self) -> str:
        return "Release Packaging & Boot Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return ctx.env.is_presubmit and not getattr(ctx.args, "aosp", False)

    def execute(self, ctx: BuildContext) -> None:
        self.release_targets = ["@goldfish//emulator:release"]
        if ctx.env.target_platform.startswith("linux"):
            self.release_targets.extend(
                [
                    "@goldfish//emulator:release_unstripped",
                    "@goldfish//emulator:release_internal",
                ]
            )
        if not ctx.env.is_windows():
            self.release_targets.append("@goldfish_test//ets:android_ets_zip")

        test_targets = [
            "@goldfish//emulator:release_build_test",
            "@goldfish_build//:sanity_checks",
            "@goldfish//emulator/launcher:boot_tests",
        ]
        if ctx.should_run_meson_generator():
            logger.info("QEMU changes detected, generating bazel build files.")
            self.release_targets.append("@qemu//google/toolchain:zip")
            test_targets.append("@qemu//google/toolchain:generated_diff")

        targets = list(self.release_targets) + test_targets

        inv = (
            BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
            .with_targets(targets)
            .with_common_flags(ctx.build_options)
            .with_build_flags(["--build_manual_tests"])
            .with_test_defaults("android_emulator/release")
            .with_profile(ctx.bazel_logs_dir / "command.profile.gz")
            .build_invocation()
        )

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        bzl.test(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=inv.timeout,
        )

        artifacts = bzl.query_artifacts(self.release_targets)
        ctx.copy_artifacts(artifacts)


class UnitTestStage(Stage):
    """Runs fast-fail unit tests with ThinLTO explicitly disabled."""

    @property
    def name(self) -> str:
        return "Fast-Fail Unit Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return ctx.env.is_presubmit and not getattr(ctx.args, "aosp", False)

    def execute(self, ctx: BuildContext) -> None:
        unit_test_targets = [
            "@goldfish//...",
            "-@goldfish//:clang_tidy_report",
        ]
        if ctx.env.is_windows():
            unit_test_targets.append("-@goldfish//emulator:external_unit_tests")

        unit_test_build_options = list(ctx.build_options) + [
            f"--build_metadata=ab_build_id={ctx.env.build_id}",
            f"--build_metadata=ab_target={ctx.env.build_target}",
            "--verbose_failures",
            "--features=-thin_lto",
            f"--@goldfish//emulator:build_id={ctx.env.build_id}",
            f"--@qemu//google/toolchain:build_id={ctx.env.build_id}",
            f"--@goldfish//emulator:is_presubmit={str(ctx.env.is_presubmit).lower()}",
        ]

        inv = (
            BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
            .with_targets(unit_test_targets)
            .with_build_flags(unit_test_build_options)
            .with_test_defaults("android_emulator/unit_tests")
            .with_profile(ctx.bazel_logs_dir / "unit_tests.profile.gz")
            .with_build_tests_only()
            .build_invocation()
        )

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        bzl.test(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=inv.timeout,
        )


class EtsIntegrationStage(Stage):
    """Runs heavy emulator integration tests with bounded memory and job limits."""

    def __init__(self, prior_bzl_instances: list[bazel.BazelCmd] | None = None):
        self.prior_bzl_instances = prior_bzl_instances or []

    @property
    def name(self) -> str:
        return "ETS Integration Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return (
            ctx.env.is_presubmit
            and not getattr(ctx.args, "aosp", False)
            and not ctx.env.is_windows()
            and ctx.env.is_release
        )

    def pre_execute(self, ctx: BuildContext) -> None:
        if _is_env_memory_constrained(ctx.env):
            # Reclaim JVM resident heap and flush file buffers on constrained hosts
            reclaim_and_stabilize_resources(
                ctx.env, ctx.active_bzl, *self.prior_bzl_instances
            )
        else:
            _log_host_memory_status(ctx.env, "ets start")

    def execute(self, ctx: BuildContext) -> None:
        ets_prefix = "@goldfish_test//ets:"
        if ctx.env.is_macos():
            ets_prefix = "@goldfish_test//ets:exclusive_"

        ets_test_targets = [
            "@goldfish_test//testlib/...",
            ets_prefix + "presubmit",
        ]

        is_constrained = _is_env_memory_constrained(ctx.env)
        inv_builder = (
            BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
            .with_targets(ets_test_targets)
            .with_common_flags(ctx.build_options)
            .with_build_flags(["--build_manual_tests"])
            .with_test_defaults("android_emulator/ets")
            .with_profile(ctx.bazel_logs_dir / "ets.profile.gz")
            .with_build_tests_only()
            .with_memory_pressure_limits(constrained=is_constrained)
        )
        if is_constrained:
            inv_builder.with_jvm_heap_limit(host_resources.get_jvm_heap_limit(ctx.env))
        inv = inv_builder.build_invocation()

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        bzl.test(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=inv.timeout,
        )


class PostsubmitStage(Stage):
    """Executes the monolithic full test matrix and release build for postsubmit."""

    def __init__(self):
        self.release_targets: list[str] = []

    @property
    def name(self) -> str:
        return "Postsubmit Full Build & Test Matrix"

    def should_run(self, ctx: BuildContext) -> bool:
        return (
            not ctx.env.is_presubmit
            and not getattr(ctx.args, "aosp", False)
            and "trusty" not in ctx.args.target
        )

    def pre_execute(self, ctx: BuildContext) -> None:
        if _is_env_memory_constrained(ctx.env):
            reclaim_and_stabilize_resources(ctx.env, ctx.active_bzl)
        else:
            _log_host_memory_status(ctx.env, "postsubmit start")

    def execute(self, ctx: BuildContext) -> None:
        self.release_targets = [
            "@goldfish//emulator:release",
            "@goldfish//emulator:package_goldfish_symbols",
            "@goldfish//emulator:package_goldfish_native_symbols",
        ]
        if ctx.env.target_platform.startswith("linux"):
            self.release_targets.extend(
                [
                    "@goldfish//emulator:release_unstripped",
                    "@goldfish//emulator:release_internal",
                ]
            )
        if not ctx.env.is_windows():
            self.release_targets.append("@goldfish_test//ets:android_ets_zip")

        test_targets = [
            "@goldfish//...",
            "-@goldfish//:clang_tidy_report",
            "-@goldfish//emulator:external_unit_tests",
            "@goldfish//emulator/launcher:boot_tests",
            "@goldfish//emulator:release_build_test",
            "@goldfish_build//:sanity_checks",
        ]

        if not ctx.env.is_windows() and ctx.env.is_release:
            prefix = (
                "@goldfish_test//ets:exclusive_"
                if ctx.env.is_macos()
                else "@goldfish_test//ets:"
            )
            test_targets.extend(
                [
                    "@goldfish_test//testlib/...",
                    prefix + "postsubmit",
                    "@goldfish//emulator:external_unit_tests",
                ]
            )

        if ctx.env.is_release and not ctx.env.is_windows() and not ctx.env.is_macos():
            test_targets.append("@goldfish_test//xts:postsubmit")

        if ctx.should_run_meson_generator():
            self.release_targets.append("@qemu//google/toolchain:zip")
            test_targets.append("@qemu//google/toolchain:generated_diff")

        aux_targets = (
            [sym_upload.Symuploader.SYM_UPLOAD_TARGET]
            if ctx.env.crashpad_symbol_server_key
            else []
        )

        is_constrained = _is_env_memory_constrained(ctx.env)
        inv_builder = (
            BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
            .with_targets(self.release_targets + aux_targets + test_targets)
            .with_common_flags(ctx.build_options)
            .with_build_flags(["--build_manual_tests"])
            .with_test_defaults("android_emulator/release")
            .with_profile(ctx.bazel_logs_dir / "command.profile.gz")
            .with_invocation_flags(["--nocache_test_results"])
            .with_fail_on_test_failure(False)
            .with_memory_pressure_limits(constrained=is_constrained)
        )
        if is_constrained:
            inv_builder.with_jvm_heap_limit(host_resources.get_jvm_heap_limit(ctx.env))
        inv = inv_builder.build_invocation()

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        postsubmit_res = bzl.test(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            fail_on_test_failure=False,
            timeout=inv.timeout,
        )

        artifacts = bzl.query_artifacts(self.release_targets)
        ctx.copy_artifacts(artifacts)

        if postsubmit_res.returncode != bazel.BuildExitCode.SUCCESS:
            logger.warning(
                "Postsubmit tests encountered failures (exit code %s).",
                postsubmit_res.returncode,
            )


class SymbolUploadStage(Stage):
    """Packages and uploads Crashpad/Breakpad symbols to production and staging servers."""

    @property
    def name(self) -> str:
        return "Crashpad Symbol Upload"

    def should_run(self, ctx: BuildContext) -> bool:
        return (
            not ctx.env.is_presubmit
            and not getattr(ctx.args, "aosp", False)
            and "trusty" not in ctx.args.target
            and bool(ctx.env.crashpad_symbol_server_key)
        )

    def execute(self, ctx: BuildContext) -> None:
        if not ctx.active_bzl:
            ctx.active_bzl = bazel.BazelCmd(
                ctx.env, startup_options=ctx.startup_options
            )

        uploader = sym_upload.Symuploader(ctx.env, ctx.active_bzl)
        symbol_zip_file = ctx.active_bzl.query_artifacts(
            ["@goldfish//emulator:package_goldfish_symbols"]
        )[0]
        uploader.upload_from_zip(
            symbol_zip_file,
            ignore_failures=ctx.env.is_windows(),
            servers=[ctx.env.crashpad_server, ctx.env.CRASHPAD_STAGING],
        )
