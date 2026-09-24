#!/usr/bin/env python3
#
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
import build_environment


class BuildScriptTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock(spec=build_environment.BuildEnvironment)
        self.mock_env.build_id = "P123456"
        self.mock_env.build_target = "linux_x64"
        self.mock_env.target_platform = "linux_x64"
        self.mock_env.is_presubmit = True
        self.mock_env.dist_dir = Path("/tmp/dist")
        self.mock_env.tmp_dir = None

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

    @patch("build.bazel.BazelCmd")
    @patch("build.copy_all")
    @patch("build.copy_bazel_logs")
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
        mock_bazel_cmd_cls.assert_called_once_with(self.mock_env, startup_options=["--startup_opt=1"])
        flags_passed = mock_bazel_cmd_cls.return_value.with_build_flags.call_args[0][0]
        self.assertIn("--config=aosp", flags_passed)
        self.assertIn("--lockfile_mode=off", flags_passed)
        self.assertIn("--config=release", flags_passed)
        self.assertIn(f"--build_metadata=ab_build_id={self.mock_env.build_id}", flags_passed)

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
        mock_copy_logs.assert_called_once_with(mock_bzl, self.mock_env.dist_dir / "logs")

    def test_get_common_build_flags(self):
        flags = build.get_common_build_flags(self.mock_env, ["--config=release"])
        self.assertIn("--config=release", flags)
        self.assertIn(f"--build_metadata=ab_build_id={self.mock_env.build_id}", flags)
        self.assertIn(f"--build_metadata=ab_target={self.mock_env.build_target}", flags)
        self.assertIn(f"--embed_label={self.mock_env.build_id}", flags)
        self.assertIn(f"--@goldfish//emulator:build_id={self.mock_env.build_id}", flags)
        self.assertIn(f"--@goldfish//emulator:is_presubmit={self.mock_env.is_presubmit}", flags)
        self.assertIn("--stamp", flags)
        self.assertIn("--verbose_failures", flags)
        self.assertIn("--config=lto", flags)


    @patch("build.bazel.BazelCmd")
    @patch("build.copy_bazel_logs")
    def test_build_aosp_copies_logs_on_failure(self, mock_copy_logs, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl
        mock_bzl.build.side_effect = build_environment.CommandFailedException("Build failed", MagicMock())

        args = argparse.Namespace(aosp=True, dist="/tmp/dist", build_id="P123456")
        with self.assertRaises(build_environment.CommandFailedException):
            build.build_aosp(
                args,
                self.mock_env,
                startup_options=[],
                build_options=["--config=release"],
            )

        mock_copy_logs.assert_called_once_with(mock_bzl, self.mock_env.dist_dir / "logs")

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


if __name__ == "__main__":
    unittest.main()
