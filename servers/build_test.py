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
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Ensure servers directory is in sys.path
_SERVERS_DIR = Path(__file__).resolve().parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

import build
from host import build_environment
from pipeline import stages


class BuildScriptTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock(spec=build_environment.BuildEnvironment)
        self.mock_env.build_id = "P123456"
        self.mock_env.build_target = "linux_x64"
        self.mock_env.host_platform = "linux"
        self.mock_env.target_platform = "linux_x64"
        self.mock_env.is_presubmit = True
        self.mock_env.is_release = True
        self.mock_env.dist_dir = Path("/tmp/dist")
        self.mock_env.tmp_dir = None
        self.mock_env.repo_root = Path("/repo")

    def test_argument_parser_aosp_flag(self):
        parser = build._create_argument_parser()
        args = parser.parse_args(["--dist", "/tmp/dist", "--aosp"])
        self.assertTrue(args.aosp)

        args_default = parser.parse_args(["--dist", "/tmp/dist"])
        self.assertFalse(args_default.aosp)

    def test_argument_parser_target_default(self):
        parser = build._create_argument_parser()
        args = parser.parse_args(["--dist", "/tmp/dist"])
        self.assertEqual(args.target, build_environment.default_target())

    def test_argument_parser_multiple_config_flags(self):
        parser = build._create_argument_parser()
        args_repeated = parser.parse_args(
            [
                "--dist",
                "/tmp/dist",
                "--config=debug",
                "--config=ci",
                "--config=remote",
            ]
        )
        self.assertEqual(args_repeated.config, ["debug", "ci", "remote"])

        args_space = parser.parse_args(
            ["--dist", "/tmp/dist", "--config", "debug", "ci"]
        )
        self.assertEqual(args_space.config, ["debug", "ci"])

    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.copy_all")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aosp_invokes_bazel_build_and_copies_artifacts(
        self, mock_copy_logs, mock_copy_all, mock_bazel_cmd_cls
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        artifact_zip = Path("/out/sdk-repo-linux-P123456.zip")
        mock_bzl.query_artifacts.return_value = [artifact_zip]

        args = argparse.Namespace(aosp=True, dist="/tmp/dist", build_id="P123456")
        build.build_aosp(
            args,
            self.mock_env,
            startup_options=["--startup_opt=1"],
            build_options=["--config=release"],
        )

        # BazelCmd must be constructed with startup options and build flags containing --config=aosp
        mock_bazel_cmd_cls.assert_called_once_with(
            self.mock_env, startup_options=["--startup_opt=1"]
        )
        flags_passed = mock_bazel_cmd_cls.return_value.with_build_flags.call_args[0][0]
        self.assertIn("--config=aosp", flags_passed)
        self.assertIn("--lockfile_mode=off", flags_passed)
        self.assertIn("--config=release", flags_passed)
        self.assertIn(
            f"--build_metadata=ab_build_id={self.mock_env.build_id}", flags_passed
        )

        # Must invoke bazel build on @goldfish//emulator:release ONLY
        mock_bzl.build.assert_called_once()
        targets_built = mock_bzl.build.call_args[0][0]
        self.assertEqual(targets_built, ["@goldfish//emulator:release"])
        self.assertTrue(mock_bzl.build.call_args[1].get("allow_analysis_cache_discard"))
        mock_bzl.test.assert_not_called()

        # Must query artifacts for release and copy to dist_dir
        mock_bzl.query_artifacts.assert_called_once_with(
            ["@goldfish//emulator:release"], allow_analysis_cache_discard=True
        )
        mock_copy_all.assert_called_once_with([artifact_zip], self.mock_env.dist_dir)
        mock_copy_logs.assert_called_once()

    def test_get_common_build_flags(self):
        flags = build.get_common_build_flags(self.mock_env, ["--config=release"])
        self.assertIn("--config=release", flags)
        self.assertIn(f"--build_metadata=ab_build_id={self.mock_env.build_id}", flags)
        self.assertIn(f"--build_metadata=ab_target={self.mock_env.build_target}", flags)
        self.assertIn(f"--embed_label={self.mock_env.build_id}", flags)
        self.assertIn(f"--@goldfish//emulator:build_id={self.mock_env.build_id}", flags)
        self.assertIn(
            f"--@goldfish//emulator:is_presubmit={str(self.mock_env.is_presubmit).lower()}",
            flags,
        )
        self.assertIn("--stamp", flags)
        self.assertIn("--verbose_failures", flags)
        self.assertNotIn("--config=lto", flags)

        # Postsubmit release on Linux enables --config=lto
        self.mock_env.is_presubmit = False
        self.mock_env.is_release = True
        postsubmit_flags = build.get_common_build_flags(
            self.mock_env, ["--config=release"]
        )
        self.assertIn("--config=lto", postsubmit_flags)

        # Postsubmit debug on Linux omits --config=lto
        self.mock_env.is_release = False
        debug_postsubmit_flags = build.get_common_build_flags(
            self.mock_env, ["--config=debug"]
        )
        self.assertNotIn("--config=lto", debug_postsubmit_flags)

    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aosp_copies_logs_on_failure(
        self, mock_copy_logs, mock_bazel_cmd_cls
    ):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        mock_bzl.build.side_effect = build_environment.CommandFailedException(
            "Build failed", MagicMock()
        )

        args = argparse.Namespace(aosp=True, dist="/tmp/dist", build_id="P123456")
        with self.assertRaises(build_environment.CommandFailedException):
            build.build_aosp(
                args,
                self.mock_env,
                startup_options=[],
                build_options=["--config=release"],
            )

        mock_copy_logs.assert_called_once()

    @patch("build.build_aosp")
    @patch("build.build_aemu")
    @patch("build.build_trusty")
    @patch("build.build_environment.create_build_environment")
    @patch("build.GeminiExplainer")
    def test_main_dispatches_to_aosp(
        self, mock_explainer, mock_create_env, mock_trusty, mock_aemu, mock_aosp
    ):
        mock_create_env.return_value.__enter__.return_value = self.mock_env
        mock_explainer.return_value.__enter__.return_value = MagicMock()

        test_argv = ["build.py", "--dist", "/tmp/dist", "--aosp"]
        with patch.object(sys, "argv", test_argv):
            build.main()

        mock_aosp.assert_called_once()
        mock_aemu.assert_not_called()
        mock_trusty.assert_not_called()

    @patch("build.build_aosp")
    @patch("build.build_aemu")
    @patch("build.build_trusty")
    @patch("build.build_environment.create_build_environment")
    @patch("build.GeminiExplainer")
    def test_main_dispatches_to_aemu_by_default(
        self, mock_explainer, mock_create_env, mock_trusty, mock_aemu, mock_aosp
    ):
        mock_create_env.return_value.__enter__.return_value = self.mock_env
        mock_explainer.return_value.__enter__.return_value = MagicMock()

        test_argv = ["build.py", "--dist", "/tmp/dist"]
        with patch.object(sys, "argv", test_argv):
            build.main()

        mock_aemu.assert_called_once()
        mock_aosp.assert_not_called()
        mock_trusty.assert_not_called()

    @patch("build.build_aosp")
    @patch("build.build_aemu")
    @patch("build.build_trusty")
    @patch("build.build_environment.create_build_environment")
    @patch("build.GeminiExplainer")
    def test_main_dispatches_to_trusty(
        self, mock_explainer, mock_create_env, mock_trusty, mock_aemu, mock_aosp
    ):
        mock_create_env.return_value.__enter__.return_value = self.mock_env
        mock_explainer.return_value.__enter__.return_value = MagicMock()

        test_argv = ["build.py", "--dist", "/tmp/dist", "--target", "trusty_x64"]
        with patch.object(sys, "argv", test_argv):
            build.main()

        mock_trusty.assert_called_once()
        mock_aosp.assert_not_called()
        mock_aemu.assert_not_called()

    def test_bazel_cmd_shutdown(self):
        bzl = build.bazel.BazelCmd(
            self.mock_env, startup_options=["--output_base=/tmp/out"]
        )
        bzl.shutdown()
        self.mock_env.run.assert_called_once()
        cmd = self.mock_env.run.call_args[0][0]
        self.assertIn("--output_base=/tmp/out", cmd)
        self.assertEqual(cmd[-1], "shutdown")
        self.assertFalse(self.mock_env.run.call_args[1].get("throw_on_failure"))

    @patch("pipeline.stages.os.sync")
    @patch("pipeline.stages._log_host_memory_status")
    def test_reclaim_and_stabilize_resources(self, mock_log_mem, mock_sync):
        mock_bzl1 = MagicMock()
        mock_bzl2 = MagicMock()
        stages.reclaim_and_stabilize_resources(self.mock_env, mock_bzl1, mock_bzl2)
        mock_bzl1.shutdown.assert_called_once()
        mock_bzl2.shutdown.assert_called_once()
        mock_sync.assert_called_once()
        self.assertEqual(mock_log_mem.call_count, 2)
        mock_log_mem.assert_has_calls(
            [
                call(self.mock_env, "before shutdown"),
                call(self.mock_env, "after shutdown"),
            ]
        )

    @patch("host.host_resources.subprocess.run")
    def test_log_host_memory_status_macos(self, mock_run):
        self.mock_env.is_macos.return_value = True
        self.mock_env.is_linux.return_value = False
        mock_run.side_effect = [
            MagicMock(
                returncode=0, stdout="total = 4096.00M used = 100.00M free = 3996.00M"
            ),
            MagicMock(
                returncode=0,
                stdout="Mach Virtual Memory Statistics:\nPages free: 1000.\nPages active: 2000.\n",
            ),
        ]
        with self.assertLogs(level="INFO") as cm:
            stages._log_host_memory_status(self.mock_env, "before shutdown")
        self.assertTrue(
            any(
                "Host memory status [before shutdown] (macOS)" in line
                for line in cm.output
            )
        )
        self.assertTrue(
            any("Host vm_stat [before shutdown]" in line for line in cm.output)
        )

    @patch("host.host_resources.pathlib.Path.exists")
    @patch("host.host_resources.pathlib.Path.read_text")
    def test_log_host_memory_status_linux(self, mock_read_text, mock_exists):
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_linux.return_value = True
        mock_exists.return_value = True
        mock_read_text.return_value = (
            "MemTotal:        16384 kB\n"
            "MemFree:          8192 kB\n"
            "MemAvailable:    12000 kB\n"
            "SwapTotal:        4096 kB\n"
            "SwapFree:         2048 kB\n"
        )
        with self.assertLogs(level="INFO") as cm:
            stages._log_host_memory_status(self.mock_env, "after shutdown")
        self.assertTrue(
            any(
                "Host memory status [after shutdown] (Linux)" in line
                for line in cm.output
            )
        )
        self.assertTrue(any("'MemTotal': '16384 kB'" in line for line in cm.output))

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.copy_all")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aemu_presubmit_executes_three_stages(
        self, mock_copy_logs, mock_copy_all, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl_release = MagicMock()
        mock_bzl_unit_tests = MagicMock()
        mock_bzl_ets = MagicMock()

        mock_bazel_cmd_cls.side_effect = [
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_release)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_unit_tests)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_ets)),
        ]

        artifact_zip = Path("/out/sdk-repo-linux-P123456.zip")
        mock_bzl_release.query_artifacts.return_value = [artifact_zip]

        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = False

        args = argparse.Namespace(
            aosp=False,
            dist="/tmp/dist",
            build_id="P123456",
            target="linux_x64",
            change_info=None,
        )

        build.build_aemu(
            args,
            self.mock_env,
            startup_options=["--startup_opt=1"],
            build_options=["--config=release"],
        )

        # Stage 1: mock_bzl_release.test called with stage 1 targets (excluding ETS)
        mock_bzl_release.test.assert_called_once()
        stage1_targets = mock_bzl_release.test.call_args[0][0]
        self.assertIn("@goldfish//emulator/launcher:boot_tests", stage1_targets)
        for t in stage1_targets:
            self.assertNotIn("@goldfish_test//ets:presubmit", t)
        mock_copy_all.assert_called_once_with([artifact_zip], self.mock_env.dist_dir)

        # Stage 2: mock_bzl_unit_tests.test called with unit test targets
        mock_bzl_unit_tests.test.assert_called_once()
        stage2_flags = mock_bzl_unit_tests.test.call_args[1]["invocation_flags"]
        self.assertIn("--build_tests_only", stage2_flags)

        # On unconstrained Linux, Bazel server should NOT be shut down before ETS
        mock_reclaim.assert_not_called()

        # Stage 3: mock_bzl_ets.test called with ETS targets and no throttling
        mock_bzl_ets.test.assert_called_once()
        stage3_targets = mock_bzl_ets.test.call_args[0][0]
        self.assertTrue(any("@goldfish_test//ets:" in t for t in stage3_targets))
        stage3_flags = mock_bzl_ets.test.call_args[1]["invocation_flags"]
        self.assertIn("--build_tests_only", stage3_flags)
        # On unconstrained Linux, RBE concurrency should not be throttled to 2 jobs
        self.assertNotIn("--jobs=2", stage3_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", stage3_flags)

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.copy_all")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aemu_presubmit_macos_constrains_jobs_and_memory(
        self, mock_copy_logs, mock_copy_all, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl_release = MagicMock()
        mock_bzl_unit_tests = MagicMock()
        mock_bzl_ets = MagicMock()

        mock_bazel_cmd_cls.side_effect = [
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_release)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_unit_tests)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_ets)),
        ]

        artifact_zip = Path("/out/sdk-repo-darwin_aarch64-P123456.zip")
        mock_bzl_release.query_artifacts.return_value = [artifact_zip]

        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = True

        args = argparse.Namespace(
            aosp=False,
            dist="/tmp/dist",
            build_id="P123456",
            target="mac_aarch64",
            change_info=None,
        )

        build.build_aemu(
            args,
            self.mock_env,
            startup_options=[],
            build_options=["--config=release"],
        )

        mock_reclaim.assert_called_once()
        stage3_flags = mock_bzl_ets.test.call_args[1]["invocation_flags"]
        self.assertIn("--jobs=2", stage3_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", stage3_flags)

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.copy_all")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aemu_postsubmit_constrains_jobs_only_in_ets_stage(
        self, mock_copy_logs, mock_copy_all, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl_release = MagicMock()
        mock_bzl_symbols = MagicMock()
        mock_bzl_unit_tests = MagicMock()
        mock_bzl_ets = MagicMock()

        mock_bazel_cmd_cls.side_effect = [
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_release)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_symbols)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_unit_tests)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_ets)),
        ]

        artifact_zip = Path("/out/sdk-repo-darwin_aarch64-123456.zip")
        symbol_zips = [
            Path("/out/sdk-repo-darwin_aarch64-breakpad-symbols-123456.zip"),
            Path("/out/sdk-repo-darwin_aarch64-native-symbols-123456.zip"),
        ]
        mock_bzl_release.query_artifacts.return_value = [artifact_zip]
        mock_bzl_symbols.query_artifacts.return_value = symbol_zips
        mock_bzl_release.test.return_value = MagicMock(returncode=0)
        mock_bzl_symbols.build.return_value = MagicMock(returncode=0)
        mock_bzl_unit_tests.test.return_value = MagicMock(returncode=0)
        mock_bzl_ets.test.return_value = MagicMock(returncode=0)

        self.mock_env.is_presubmit = False
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = True
        self.mock_env.is_memory_constrained.return_value = True
        self.mock_env.crashpad_symbol_server_key = None

        args = argparse.Namespace(
            aosp=False,
            dist="/tmp/dist",
            build_id="123456",
            target="mac_aarch64",
            change_info=None,
        )

        build.build_aemu(
            args,
            self.mock_env,
            startup_options=[],
            build_options=["--config=release"],
        )

        # Stage 1 (ReleasePackageStage), Stage 2 (SymbolUploadStage), and Stage 3 (UnitTestStage) run unthrottled
        mock_bzl_release.test.assert_called_once()
        stage1_flags = mock_bzl_release.test.call_args[1]["invocation_flags"]
        self.assertNotIn("--jobs=2", stage1_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", stage1_flags)

        mock_bzl_symbols.build.assert_called_once()
        mock_bzl_unit_tests.test.assert_called_once()
        stage3_flags = mock_bzl_unit_tests.test.call_args[1]["invocation_flags"]
        self.assertNotIn("--jobs=2", stage3_flags)

        # Stage 4 (EtsIntegrationStage) reclaims resources and throttles to --jobs=2
        mock_reclaim.assert_called_once_with(self.mock_env, mock_bzl_unit_tests)
        mock_bzl_ets.test.assert_called_once()
        stage4_targets = mock_bzl_ets.test.call_args[0][0]
        self.assertIn("@goldfish_test//ets:exclusive_postsubmit", stage4_targets)
        stage4_flags = mock_bzl_ets.test.call_args[1]["invocation_flags"]
        self.assertIn("--jobs=2", stage4_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", stage4_flags)

    @patch("pipeline.stages.reclaim_and_stabilize_resources")
    @patch("pipeline.stages.bazel.BazelCmd")
    @patch("pipeline.context.copy_all")
    @patch("pipeline.context.BuildContext.copy_bazel_logs")
    def test_build_aemu_postsubmit_unconstrained_preserves_rbe_jobs(
        self, mock_copy_logs, mock_copy_all, mock_bazel_cmd_cls, mock_reclaim
    ):
        mock_bzl_release = MagicMock()
        mock_bzl_symbols = MagicMock()
        mock_bzl_unit_tests = MagicMock()
        mock_bzl_ets = MagicMock()

        mock_bazel_cmd_cls.side_effect = [
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_release)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_symbols)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_unit_tests)),
            MagicMock(with_build_flags=MagicMock(return_value=mock_bzl_ets)),
        ]

        artifact_zip = Path("/out/sdk-repo-linux-123456.zip")
        symbol_zips = [
            Path("/out/sdk-repo-linux-breakpad-symbols-123456.zip"),
            Path("/out/sdk-repo-linux-native-symbols-123456.zip"),
        ]
        mock_bzl_release.query_artifacts.return_value = [artifact_zip]
        mock_bzl_symbols.query_artifacts.return_value = symbol_zips
        mock_bzl_release.test.return_value = MagicMock(returncode=0)
        mock_bzl_symbols.build.return_value = MagicMock(returncode=0)
        mock_bzl_unit_tests.test.return_value = MagicMock(returncode=0)
        mock_bzl_ets.test.return_value = MagicMock(returncode=0)

        self.mock_env.is_presubmit = False
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = False
        self.mock_env.crashpad_symbol_server_key = None

        args = argparse.Namespace(
            aosp=False,
            dist="/tmp/dist",
            build_id="123456",
            target="linux_x64",
            change_info=None,
        )

        build.build_aemu(
            args,
            self.mock_env,
            startup_options=[],
            build_options=["--config=release"],
        )

        mock_reclaim.assert_not_called()
        mock_bzl_release.test.assert_called_once()
        mock_bzl_symbols.build.assert_called_once()
        mock_bzl_unit_tests.test.assert_called_once()
        mock_bzl_ets.test.assert_called_once()
        ets_flags = mock_bzl_ets.test.call_args[1]["invocation_flags"]
        self.assertNotIn("--jobs=2", ets_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", ets_flags)


if __name__ == "__main__":
    unittest.main()
