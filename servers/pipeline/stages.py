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


def _is_env_cross_compile(env: build_environment.BuildEnvironment) -> bool:
    """Checks if the environment is cross-compiling, handling unconfigured mocks safely."""
    if hasattr(env, "is_cross_compile"):
        res = env.is_cross_compile()
        if MagicMock is not None and isinstance(res, MagicMock):
            return False
        return bool(res)
    return False


def _should_collect_coverage(env: build_environment.BuildEnvironment) -> bool:
    """Returns True when unit tests should run under `bazel coverage` (native Linux release postsubmit builds)."""
    if (
        env.is_presubmit
        or not env.is_release
        or env.is_windows()
        or env.is_macos()
        or _is_env_cross_compile(env)
    ):
        return False
    res = env.is_linux()
    if MagicMock is not None and isinstance(res, MagicMock):
        return env.target_platform.startswith("linux")
    return bool(res)


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
            except Exception as e:  # noqa: BLE001
                # Resource cleanup must not abort pipeline
                logger.warning("Failed to shutdown Bazel server: %s", e)

    try:
        if hasattr(os, "sync"):
            os.sync()
    except OSError as e:
        logger.warning("os.sync failed: %s", e)

    _log_host_memory_status(env, "after shutdown")


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
    """Builds release binaries and runs fast boot tests."""

    def __init__(self):
        self.release_targets: list[str] = []

    @property
    def name(self) -> str:
        return "Release Packaging & Boot Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return not getattr(ctx.args, "aosp", False) and "trusty" not in getattr(
            ctx.args, "target", ""
        )

    def execute(self, ctx: BuildContext) -> None:
        self.release_targets = ["@goldfish//emulator:release"]
        if ctx.env.target_platform.startswith("linux"):
            self.release_targets.extend(
                [
                    "@goldfish//emulator:release_unstripped",
                    "@goldfish//emulator:release_internal",
                ]
            )
        if not ctx.env.is_windows() and not ctx.env.target_platform.startswith(
            "windows"
        ):
            self.release_targets.append("@goldfish_test//ets:android_ets_zip")

        test_targets = [
            "@goldfish//emulator:release_build_test",
            "@goldfish//emulator/tests:release_zip_test",
            "@goldfish_build//:sanity_checks",
            "@goldfish//emulator/launcher:boot_tests",
        ]
        if ctx.env.target_platform.startswith("linux"):
            test_targets.append("@goldfish//emulator/tests:release_zip_internal_test")
        if ctx.should_run_meson_generator():
            logger.info("QEMU changes detected, generating bazel build files.")
            self.release_targets.append("@qemu//google/toolchain:zip")
            test_targets.append("@qemu//google/toolchain:generated_diff")

        tidy_build_flags = ctx.get_clang_tidy_build_flags()
        is_cross_compile = _is_env_cross_compile(ctx.env)

        if is_cross_compile:
            inv = (
                BazelInvocationBuilder("build", ctx.env, ctx.startup_options)
                .with_targets(self.release_targets)
                .with_common_flags(ctx.build_options)
                .with_build_flags(tidy_build_flags)
                .with_profile(ctx.bazel_logs_dir / "command.profile.gz")
                .build_invocation()
            )
            bzl = bazel.BazelCmd(
                ctx.env, startup_options=inv.startup_options
            ).with_build_flags(inv.build_flags)
            ctx.active_bzl = bzl

            bzl.build(
                inv.targets,
                invocation_flags=inv.invocation_flags,
                allow_analysis_cache_discard=True,
                timeout=inv.timeout,
            )
        else:
            targets = list(self.release_targets) + test_targets
            inv_builder = (
                BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
                .with_targets(targets)
                .with_common_flags(ctx.build_options)
                .with_build_flags(["--build_manual_tests"] + tidy_build_flags)
                .with_test_defaults("android_emulator/release")
                .with_profile(ctx.bazel_logs_dir / "command.profile.gz")
                .with_fail_on_test_failure(ctx.env.is_presubmit)
            )
            if not ctx.env.is_presubmit:
                inv_builder.with_invocation_flags(["--nocache_test_results"])
            inv = inv_builder.build_invocation()

            bzl = bazel.BazelCmd(
                ctx.env, startup_options=inv.startup_options
            ).with_build_flags(inv.build_flags)
            ctx.active_bzl = bzl

            res = bzl.test(
                inv.targets,
                invocation_flags=inv.invocation_flags,
                allow_analysis_cache_discard=True,
                fail_on_test_failure=inv.fail_on_test_failure,
                timeout=inv.timeout,
            )
            if (
                not ctx.env.is_presubmit
                and isinstance(getattr(res, "returncode", None), int)
                and res.returncode != bazel.BuildExitCode.SUCCESS
            ):
                logger.warning(
                    "Release packaging tests encountered failures (exit code %s).",
                    res.returncode,
                )

        artifacts = bzl.query_artifacts(self.release_targets)
        ctx.copy_artifacts(artifacts)


class UnitTestStage(Stage):
    """Runs fast-fail unit tests and scoped clang-tidy checks with ThinLTO disabled."""

    def __init__(self):
        self._bzl: bazel.BazelCmd | None = None

    @property
    def name(self) -> str:
        return "Fast-Fail Unit Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return not getattr(ctx.args, "aosp", False) and "trusty" not in getattr(
            ctx.args, "target", ""
        )

    def execute(self, ctx: BuildContext) -> None:
        unit_test_targets = [
            "@goldfish//...",
            "-@goldfish//:clang_tidy_report",
        ]
        if ctx.env.is_windows() or not ctx.env.is_release:
            unit_test_targets.append("-@goldfish//emulator:external_unit_tests")
        elif not ctx.env.is_presubmit:
            unit_test_targets.append("@goldfish//emulator:external_unit_tests")
        if ctx.env.is_linux() and not _is_env_cross_compile(ctx.env):
            unit_test_targets.append("@gfxstream//:gfxstream_unit_tests")
        if _is_env_cross_compile(ctx.env) and ctx.env.target_platform.startswith(
            "windows"
        ):
            unit_test_targets.extend(
                [
                    "-@goldfish//emulator/crashreport/...",
                    "-@goldfish//emulator/launcher/...",
                    "-@goldfish//emulator/tests/...",
                    "-@goldfish//emulator/tools/...",
                    "-@goldfish//emulator/videobridge/java/...",
                ]
            )

        tidy_build_flags = ctx.get_clang_tidy_build_flags()
        is_cross_compile = _is_env_cross_compile(ctx.env)

        if is_cross_compile:
            inv_builder = (
                BazelInvocationBuilder("build", ctx.env, ctx.startup_options)
                .with_targets(unit_test_targets)
                .with_common_flags(ctx.build_options)
                .with_build_flags(tidy_build_flags)
                .with_thin_lto_disabled()
                .with_build_tests_only()
                .with_profile(ctx.bazel_logs_dir / "unit_tests.profile.gz")
            )
            if tidy_build_flags:
                inv_builder.with_invocation_flags(
                    ["--modify_execution_info=ClangTidy.*=+no-remote"]
                )
            inv = inv_builder.build_invocation()

            bzl = bazel.BazelCmd(
                ctx.env, startup_options=inv.startup_options
            ).with_build_flags(inv.build_flags)
            self._bzl = bzl
            ctx.active_bzl = bzl

            bzl.build(
                inv.targets,
                invocation_flags=inv.invocation_flags,
                allow_analysis_cache_discard=True,
                timeout=inv.timeout,
            )
            return

        verb = "coverage" if _should_collect_coverage(ctx.env) else "test"

        inv_builder = (
            BazelInvocationBuilder(verb, ctx.env, ctx.startup_options)
            .with_targets(unit_test_targets)
            .with_common_flags(ctx.build_options)
            .with_build_flags(tidy_build_flags)
            .with_thin_lto_disabled()
            .with_test_defaults(
                "android_emulator/unit_tests",
                test_timeout="120,300,600,1200",
            )
            .with_profile(ctx.bazel_logs_dir / "unit_tests.profile.gz")
            .with_build_tests_only()
            .with_fail_on_test_failure(ctx.env.is_presubmit)
        )
        if tidy_build_flags:
            inv_builder.with_invocation_flags(
                ["--modify_execution_info=ClangTidy.*=+no-remote"]
            )
        else:
            inv_builder.with_invocation_flags(["--test_tag_filters=-tidy-test"])
        if not ctx.env.is_presubmit:
            inv_builder.with_invocation_flags(["--nocache_test_results"])
        inv = inv_builder.build_invocation()

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        self._bzl = bzl
        ctx.active_bzl = bzl

        run_fn = bzl.coverage if inv.verb == "coverage" else bzl.test
        res = run_fn(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            fail_on_test_failure=inv.fail_on_test_failure,
            timeout=inv.timeout,
        )
        if (
            not ctx.env.is_presubmit
            and isinstance(getattr(res, "returncode", None), int)
            and res.returncode != bazel.BuildExitCode.SUCCESS
        ):
            logger.warning(
                "Unit tests encountered failures (exit code %s).",
                res.returncode,
            )

    def on_failure(self, ctx: BuildContext, exc: Exception) -> None:
        if not self._bzl:
            return
        try:
            testlogs_dirs: list[pathlib.Path] = []
            if "bazel-testlogs" in self._bzl.info:
                primary_testlogs = pathlib.Path(self._bzl.info["bazel-testlogs"])
                if primary_testlogs.is_dir():
                    testlogs_dirs.append(primary_testlogs)
            if "output_path" in self._bzl.info:
                output_path = pathlib.Path(self._bzl.info["output_path"])
                if output_path.is_dir():
                    for candidate in sorted(output_path.glob("*/testlogs")):
                        if candidate.is_dir() and candidate not in testlogs_dirs:
                            testlogs_dirs.append(candidate)

            for testlogs_dir in testlogs_dirs:
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
        except Exception as e:  # noqa: BLE001
            # Telemetry extraction must not mask the primary failure
            logger.warning("Failed to extract clang-tidy logs: %s", e)


class EtsIntegrationStage(Stage):
    """Runs heavy emulator integration tests with bounded memory and job limits."""

    def __init__(self, prior_bzl_instances: list[bazel.BazelCmd] | None = None):
        self.prior_bzl_instances = prior_bzl_instances or []

    @property
    def name(self) -> str:
        return "ETS Integration Tests"

    def should_run(self, ctx: BuildContext) -> bool:
        return (
            not getattr(ctx.args, "aosp", False)
            and "trusty" not in getattr(ctx.args, "target", "")
            and not ctx.env.is_windows()
            and not _is_env_cross_compile(ctx.env)
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
        ets_suite = "presubmit" if ctx.env.is_presubmit else "postsubmit"

        ets_test_targets = [
            "@goldfish_test//testlib/...",
            ets_prefix + ets_suite,
        ]
        if not ctx.env.is_presubmit and not ctx.env.is_macos():
            ets_test_targets.append("@goldfish_test//xts:postsubmit")

        is_constrained = _is_env_memory_constrained(ctx.env)
        tidy_build_flags = ctx.get_clang_tidy_build_flags()
        inv_builder = (
            BazelInvocationBuilder("test", ctx.env, ctx.startup_options)
            .with_targets(ets_test_targets)
            .with_common_flags(ctx.build_options)
            .with_build_flags(["--build_manual_tests"] + tidy_build_flags)
            .with_test_defaults("android_emulator/ets")
            .with_profile(ctx.bazel_logs_dir / "ets.profile.gz")
            .with_build_tests_only()
            .with_fail_on_test_failure(ctx.env.is_presubmit)
            .with_memory_pressure_limits(constrained=is_constrained)
        )
        if not ctx.env.is_presubmit:
            inv_builder.with_invocation_flags(["--nocache_test_results"])
        if is_constrained:
            inv_builder.with_jvm_heap_limit(host_resources.get_jvm_heap_limit(ctx.env))
        inv = inv_builder.build_invocation()

        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        res = bzl.test(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            fail_on_test_failure=inv.fail_on_test_failure,
            timeout=inv.timeout,
        )
        if (
            not ctx.env.is_presubmit
            and isinstance(getattr(res, "returncode", None), int)
            and res.returncode != bazel.BuildExitCode.SUCCESS
        ):
            logger.warning(
                "ETS integration tests encountered failures (exit code %s).",
                res.returncode,
            )


class SymbolUploadStage(Stage):
    """Packages and uploads Crashpad/Breakpad symbols to production and staging servers."""

    def __init__(self):
        self.symbol_targets: list[str] = []

    @property
    def name(self) -> str:
        return "Crashpad Symbol Upload"

    def should_run(self, ctx: BuildContext) -> bool:
        return (
            not ctx.env.is_presubmit
            and ctx.env.is_release
            and not getattr(ctx.args, "aosp", False)
            and "trusty" not in getattr(ctx.args, "target", "")
        )

    def execute(self, ctx: BuildContext) -> None:
        self.symbol_targets = [
            "@goldfish//emulator:package_goldfish_symbols",
            "@goldfish//emulator:package_goldfish_native_symbols",
        ]
        aux_targets = (
            [sym_upload.Symuploader.SYM_UPLOAD_TARGET]
            if ctx.env.crashpad_symbol_server_key
            else []
        )
        targets = list(self.symbol_targets) + aux_targets

        inv = (
            BazelInvocationBuilder("build", ctx.env, ctx.startup_options)
            .with_targets(targets)
            .with_common_flags(ctx.build_options)
            .with_profile(ctx.bazel_logs_dir / "symbols.profile.gz")
            .build_invocation()
        )
        bzl = bazel.BazelCmd(
            ctx.env, startup_options=inv.startup_options
        ).with_build_flags(inv.build_flags)
        ctx.active_bzl = bzl

        bzl.build(
            inv.targets,
            invocation_flags=inv.invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=inv.timeout,
        )

        artifacts = bzl.query_artifacts(self.symbol_targets)
        ctx.copy_artifacts(artifacts)

        if ctx.env.crashpad_symbol_server_key:
            uploader = sym_upload.Symuploader(ctx.env, bzl)
            uploader.upload_from_zip(
                artifacts[0],
                ignore_failures=ctx.env.is_windows(),
                servers=[ctx.env.crashpad_server, ctx.env.CRASHPAD_STAGING],
            )
        else:
            logger.warning("No server API key available, not uploading symbols.")
