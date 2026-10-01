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
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from host import build_environment
from pipeline import stages
from pipeline.context import BuildContext, copy_all, safe_copy_file
from pipeline.pipeline import Pipeline
from pipeline.stage import Stage


class DummyStage(Stage):

    def __init__(self, stage_name: str, should_run_val: bool = True):
        self._name = stage_name
        self._should_run = should_run_val
        self.pre_executed = False
        self.executed = False
        self.failed_with = None

    @property
    def name(self) -> str:
        return self._name

    def should_run(self, ctx: BuildContext) -> bool:
        return self._should_run

    def pre_execute(self, ctx: BuildContext) -> None:
        self.pre_executed = True

    def execute(self, ctx: BuildContext) -> None:
        self.executed = True

    def on_failure(self, ctx: BuildContext, exc: Exception) -> None:
        self.failed_with = exc


class FailingStage(Stage):

    @property
    def name(self) -> str:
        return "Failing Stage"

    def execute(self, ctx: BuildContext) -> None:
        raise RuntimeError("Stage failure test")

    def on_failure(self, ctx: BuildContext, exc: Exception) -> None:
        self.failure_recorded = exc


class PipelineTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock(spec=build_environment.BuildEnvironment)
        self.mock_env.build_id = "P123"
        self.mock_env.build_target = "linux_x64"
        self.mock_env.target_platform = "linux_x64"
        self.mock_env.is_presubmit = True
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_windows.return_value = False
        self.mock_env.dist_dir = Path("/tmp/dist")
        self.mock_env.crashpad_symbol_server_key = "key123"

        self.args = argparse.Namespace(
            dist="/tmp/dist",
            build_id="P123",
            target="linux_x64",
            aosp=False,
            change_info=None,
            config=["release"],
        )
        self.ctx = BuildContext(
            args=self.args,
            env=self.mock_env,
            startup_options=["--startup=1"],
            build_options=["--config=release"],
        )
        self.ctx.copy_bazel_logs = MagicMock()
        self.ctx.copy_artifacts = MagicMock()

    def test_pipeline_executes_stages_in_order(self):
        s1 = DummyStage("First")
        s2 = DummyStage("Second")
        pipeline = Pipeline(self.ctx, [s1, s2])
        with self.assertLogs(level="INFO") as cm:
            pipeline.run()

        self.assertTrue(s1.pre_executed)
        self.assertTrue(s1.executed)
        self.assertTrue(s2.pre_executed)
        self.assertTrue(s2.executed)
        self.ctx.copy_bazel_logs.assert_called_once()
        self.assertTrue(any("STARTING STAGE 1: First" in line for line in cm.output))
        self.assertTrue(any("STARTING STAGE 2: Second" in line for line in cm.output))

    def test_stage_names_exclude_stage_numbers(self):
        all_stages = [
            stages.ClangTidyStage(),
            stages.AospStage(),
            stages.ReleasePackageStage(),
            stages.UnitTestStage(),
            stages.EtsIntegrationStage(),
            stages.SymbolUploadStage(),
        ]
        for s in all_stages:
            self.assertNotIn("Stage ", s.name)

    def test_pipeline_skips_inactive_stages(self):
        s1 = DummyStage("Active Stage", should_run_val=True)
        s2 = DummyStage("Inactive Stage", should_run_val=False)
        pipeline = Pipeline(self.ctx, [s1, s2])
        pipeline.run()

        self.assertTrue(s1.executed)
        self.assertFalse(s2.pre_executed)
        self.assertFalse(s2.executed)

    def test_pipeline_catches_failure_and_guarantees_logs(self):
        s_fail = FailingStage()
        pipeline = Pipeline(self.ctx, [s_fail])

        with self.assertRaises(RuntimeError):
            pipeline.run()

        self.assertIsNotNone(s_fail.failure_recorded)
        self.ctx.copy_bazel_logs.assert_called_once()

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_aosp_stage(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        artifact_zip = Path("/out/aosp.zip")
        mock_bzl.query_artifacts.return_value = [artifact_zip]

        self.ctx.args.aosp = True
        stage = stages.AospStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)

        mock_bzl.build.assert_called_once()
        targets = mock_bzl.build.call_args[0][0]
        self.assertEqual(targets, ["@goldfish//emulator:release"])
        self.ctx.copy_artifacts.assert_called_once_with([artifact_zip])

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_release_package_stage_presubmit(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        artifact_zip = Path("/out/release.zip")
        mock_bzl.query_artifacts.return_value = [artifact_zip]

        stage = stages.ReleasePackageStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)

        mock_bzl.test.assert_called_once()
        targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@goldfish//emulator:release", targets)
        self.assertIn("@goldfish//emulator/launcher:boot_tests", targets)
        self.ctx.copy_artifacts.assert_called_once_with([artifact_zip])

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_release_package_stage_with_qemu_changes(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        artifact_zips = [
            Path("/out/release.zip"),
            Path("/out/bazel-linux-x86_64-P123.zip"),
        ]
        mock_bzl.query_artifacts.return_value = artifact_zips
        self.ctx.should_run_meson_generator = MagicMock(return_value=True)

        stage = stages.ReleasePackageStage()
        stage.execute(self.ctx)

        mock_bzl.test.assert_called_once()
        targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@qemu//google/toolchain:zip", targets)
        self.assertIn("@qemu//google/toolchain:generated_diff", targets)
        self.assertIn("@qemu//google/toolchain:zip", stage.release_targets)
        mock_bzl.query_artifacts.assert_called_once_with(stage.release_targets)
        self.ctx.copy_artifacts.assert_called_once_with(artifact_zips)

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_unit_test_stage_presubmit(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl

        stage = stages.UnitTestStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)

        mock_bzl.test.assert_called_once()
        targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@goldfish//...", targets)
        self.assertIn("-@goldfish//:clang_tidy_report", targets)

        # Build flags must disable ThinLTO
        flags_passed = mock_bazel_cmd_cls.return_value.with_build_flags.call_args[0][0]
        self.assertIn("--features=-thin_lto", flags_passed)

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_ets_integration_stage(self, mock_bazel_cmd_cls, mock_reclaim):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        self.mock_env.is_memory_constrained.return_value = True

        stage = stages.EtsIntegrationStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.pre_execute(self.ctx)
        mock_reclaim.assert_called_once_with(self.mock_env, self.ctx.active_bzl)

        stage.execute(self.ctx)
        mock_bzl.test.assert_called_once()
        targets = mock_bzl.test.call_args[0][0]
        self.assertTrue(any("@goldfish_test//ets:" in t for t in targets))

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_ets_integration_stage_macos_limits(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        self.mock_env.is_macos.return_value = True

        stage = stages.EtsIntegrationStage()
        stage.execute(self.ctx)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertIn("--jobs=2", invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", invocation_flags)
        startup_opts = mock_bazel_cmd_cls.call_args[1]["startup_options"]
        self.assertIn("--host_jvm_args=-Xmx1g", startup_opts)

    @patch("pipeline.stages.sym_upload.Symuploader")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_symbol_upload_stage_postsubmit(
        self, mock_bazel_cmd_cls, mock_uploader_cls
    ):
        self.mock_env.is_presubmit = False
        self.mock_env.crashpad_server = "https://prod.crashpad"
        self.mock_env.CRASHPAD_STAGING = "https://staging.crashpad"
        mock_bzl = MagicMock()
        mock_bzl.build_flags = ("--config=release",)
        mock_bzl.query_artifacts.return_value = [Path("/out/symbols.zip")]
        self.ctx.active_bzl = mock_bzl

        mock_uploader = MagicMock()
        mock_uploader_cls.return_value = mock_uploader

        stage = stages.SymbolUploadStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)

        # Must reuse ctx.active_bzl without mutating build flags (preserves analysis cache)
        mock_bzl.with_build_flags.assert_not_called()
        mock_uploader_cls.assert_called_once_with(self.mock_env, mock_bzl)
        mock_bzl.query_artifacts.assert_called_once_with(
            ["@goldfish//emulator:package_goldfish_symbols"]
        )
        mock_uploader.upload_from_zip.assert_called_once_with(
            Path("/out/symbols.zip"),
            ignore_failures=False,
            servers=["https://prod.crashpad", "https://staging.crashpad"],
        )

    @patch("pipeline.stages.sym_upload.Symuploader")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_symbol_upload_stage_postsubmit_windows(
        self, mock_bazel_cmd_cls, mock_uploader_cls
    ):
        self.mock_env.is_presubmit = False
        self.mock_env.is_windows.return_value = True
        self.mock_env.crashpad_server = "https://prod.crashpad"
        self.mock_env.CRASHPAD_STAGING = "https://staging.crashpad"
        mock_bzl = MagicMock()
        mock_bzl.query_artifacts.return_value = [Path("C:/out/symbols.zip")]
        self.ctx.active_bzl = mock_bzl

        mock_uploader = MagicMock()
        mock_uploader_cls.return_value = mock_uploader

        stage = stages.SymbolUploadStage()
        stage.execute(self.ctx)

        mock_bzl.with_build_flags.assert_not_called()
        mock_bzl.query_artifacts.assert_called_once_with(
            ["@goldfish//emulator:package_goldfish_symbols"]
        )
        mock_uploader.upload_from_zip.assert_called_once_with(
            Path("C:/out/symbols.zip"),
            ignore_failures=True,
            servers=["https://prod.crashpad", "https://staging.crashpad"],
        )

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_ets_integration_stage_arbitrary_system_constrained(
        self, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = True

        stage = stages.EtsIntegrationStage()
        stage.pre_execute(self.ctx)
        mock_reclaim.assert_called_once_with(self.mock_env, self.ctx.active_bzl)

        stage.execute(self.ctx)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertIn("--jobs=2", invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", invocation_flags)
        startup_opts = mock_bazel_cmd_cls.call_args[1]["startup_options"]
        self.assertIn("--host_jvm_args=-Xmx4g", startup_opts)

    @patch("pipeline.stages._log_host_memory_status")
    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_ets_integration_stage_arbitrary_system_unconstrained(
        self, mock_bazel_cmd_cls, mock_reclaim, mock_log_mem
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = False

        stage = stages.EtsIntegrationStage()
        stage.pre_execute(self.ctx)
        mock_reclaim.assert_not_called()
        mock_log_mem.assert_called_once_with(self.mock_env, "ets start")

        stage.execute(self.ctx)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertNotIn("--jobs=2", invocation_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", invocation_flags)
        startup_opts = mock_bazel_cmd_cls.call_args[1]["startup_options"]
        self.assertFalse(any("-Xmx" in opt for opt in startup_opts))

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_release_package_stage_postsubmit_unthrottled_and_builds_symbols(
        self, mock_bazel_cmd_cls
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        mock_bzl.query_artifacts.return_value = [Path("/out/rel.zip")]
        mock_bzl.test.return_value = MagicMock(returncode=0)

        self.mock_env.is_presubmit = False
        self.mock_env.is_memory_constrained.return_value = True
        self.mock_env.is_macos.return_value = True
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True

        stage = stages.ReleasePackageStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)
        mock_bzl.test.assert_called_once()
        tested_targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@goldfish//emulator:package_goldfish_symbols", tested_targets)
        self.assertIn(
            "@goldfish//emulator:package_goldfish_native_symbols", tested_targets
        )
        self.assertIn("@breakpad//:sym_upload", tested_targets)
        self.assertNotIn("@breakpad//:sym_upload", stage.release_targets)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertIn("--nocache_test_results", invocation_flags)
        self.assertNotIn("--jobs=2", invocation_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", invocation_flags)
        self.assertFalse(mock_bzl.test.call_args[1]["fail_on_test_failure"])

        startup_opts = mock_bazel_cmd_cls.call_args[1]["startup_options"]
        self.assertFalse(any("-Xmx" in opt for opt in startup_opts))

    @patch("pipeline.stages.bazel.BazelCmd")
    def test_unit_test_stage_postsubmit_unthrottled_and_includes_external_tests(
        self, mock_bazel_cmd_cls
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        mock_bzl.test.return_value = MagicMock(returncode=0)

        self.mock_env.is_presubmit = False
        self.mock_env.is_memory_constrained.return_value = True
        self.mock_env.is_macos.return_value = True
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True

        stage = stages.UnitTestStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.execute(self.ctx)
        mock_bzl.test.assert_called_once()
        tested_targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@goldfish//emulator:external_unit_tests", tested_targets)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertIn("--nocache_test_results", invocation_flags)
        self.assertNotIn("--jobs=2", invocation_flags)
        self.assertFalse(mock_bzl.test.call_args[1]["fail_on_test_failure"])

        # Debug postsubmit must exclude external_unit_tests
        self.mock_env.is_release = False
        mock_bzl.test.reset_mock()
        stage.execute(self.ctx)
        debug_targets = mock_bzl.test.call_args[0][0]
        self.assertIn("-@goldfish//emulator:external_unit_tests", debug_targets)

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    def test_ets_integration_stage_postsubmit_and_skipped_in_debug(
        self, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        mock_bzl.test.return_value = MagicMock(returncode=0)

        self.mock_env.is_presubmit = False
        self.mock_env.is_memory_constrained.return_value = True
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True

        stage = stages.EtsIntegrationStage()
        self.assertTrue(stage.should_run(self.ctx))

        stage.pre_execute(self.ctx)
        mock_reclaim.assert_called_once_with(self.mock_env, self.ctx.active_bzl)

        stage.execute(self.ctx)
        tested_targets = mock_bzl.test.call_args[0][0]
        self.assertIn("@goldfish_test//ets:postsubmit", tested_targets)
        self.assertIn("@goldfish_test//xts:postsubmit", tested_targets)

        invocation_flags = mock_bzl.test.call_args[1]["invocation_flags"]
        self.assertIn("--nocache_test_results", invocation_flags)
        self.assertIn("--jobs=2", invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", invocation_flags)
        self.assertFalse(mock_bzl.test.call_args[1]["fail_on_test_failure"])

        # Debug postsubmit (e.g. emulator_mac_aarch64_debug) must skip EtsIntegrationStage
        self.mock_env.is_release = False
        self.assertFalse(stage.should_run(self.ctx))


class ContextCopyTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_safe_copy_file_overwrites_readonly_destination(self):
        src = self.test_dir / "src.log"
        src.write_text("updated content")
        dest = self.test_dir / "dest.log"
        dest.write_text("old content")
        # Mark destination read-only
        dest.chmod(stat.S_IREAD)

        out = safe_copy_file(src, dest)
        self.assertEqual(out.read_text(), "updated content")
        # Verify destination file now has user-write permission
        self.assertTrue(bool(out.stat().st_mode & stat.S_IWUSR))

    def test_copy_all_handles_readonly_destination_files(self):
        src_file = self.test_dir / "test.zip"
        src_file.write_text("zip data")
        dest_dir = self.test_dir / "dist"
        dest_dir.mkdir(parents=True, exist_ok=True)
        existing_dest = dest_dir / "test.zip"
        existing_dest.write_text("stale data")
        existing_dest.chmod(stat.S_IREAD)

        copy_all([src_file], dest_dir)
        self.assertEqual(existing_dest.read_text(), "zip data")
        self.assertTrue(bool(existing_dest.stat().st_mode & stat.S_IWUSR))

    def test_copy_bazel_logs_overwrites_readonly_testlogs(self):
        mock_env = MagicMock()
        mock_env.dist_dir = self.test_dir / "dist"
        mock_env.is_presubmit = True
        mock_env.is_release = True
        mock_env.is_windows.return_value = False
        mock_env.is_macos.return_value = False

        args = argparse.Namespace(dist=str(mock_env.dist_dir), change_info=None)
        ctx = BuildContext(args, mock_env)

        # Setup mock bazel with real testlog and server log files
        fake_output_base = self.test_dir / "output_base"
        fake_output_base.mkdir(parents=True, exist_ok=True)
        (fake_output_base / "server").mkdir(parents=True, exist_ok=True)
        jvm_out = fake_output_base / "server" / "jvm.out"
        jvm_out.write_text("jvm log")

        server_log = fake_output_base / "server.log"
        server_log.write_text("server log")

        fake_testlogs = self.test_dir / "bazel-testlogs"
        test_log_dir = fake_testlogs / "external" / "pkg" / "my_test"
        test_log_dir.mkdir(parents=True, exist_ok=True)
        src_test_log = test_log_dir / "test.log"
        src_test_log.write_text("new test log")
        # Source log produced by Bazel is read-only
        src_test_log.chmod(stat.S_IREAD)

        # Destination test log already exists from a prior run and is read-only
        dest_test_log = (
            ctx.testlogs_dest_dir / "external" / "pkg" / "my_test" / "test.log"
        )
        dest_test_log.parent.mkdir(parents=True, exist_ok=True)
        dest_test_log.write_text("stale test log")
        dest_test_log.chmod(stat.S_IREAD)

        mock_bzl = MagicMock()
        mock_bzl.info = {
            "output_base": str(fake_output_base),
            "server_log": str(server_log),
            "bazel-testlogs": str(fake_testlogs),
        }

        # Must not raise PermissionError or fail
        ctx.copy_bazel_logs(mock_bzl)

        self.assertEqual(dest_test_log.read_text(), "new test log")
        self.assertTrue(bool(dest_test_log.stat().st_mode & stat.S_IWUSR))


if __name__ == "__main__":
    unittest.main()
