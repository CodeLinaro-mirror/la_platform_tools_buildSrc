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
import asyncio
import logging
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

logger = logging.getLogger(__name__)

_SERVERS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from host import build_environment


class BuildEnvironmentTest(unittest.TestCase):

    def test_default_target(self):
        target = build_environment.default_target()
        self.assertIn(target, ["linux_x64", "mac_aarch64", "windows_x64"])

    def test_find_repo_root_env_override(self):
        env = {"BUILD_WORKSPACE_DIRECTORY": "/custom/workspace"}
        root = build_environment.find_repo_root(env)
        self.assertEqual(root, pathlib.Path("/custom/workspace"))

    def test_find_repo_root_raises_when_no_markers(self):
        with (
            patch("pathlib.Path.exists", return_value=False),
            self.assertRaises(FileNotFoundError),
        ):
            build_environment.find_repo_root({})

    @patch("host.build_environment.platform.system")
    def test_create_build_environment_linux(self, mock_system):
        mock_system.return_value = "Linux"
        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P123", target="linux_x64", config=["release"]
        )
        env = build_environment.create_build_environment(args)
        self.assertIsInstance(env, build_environment.LinuxBuildEnvironment)
        self.assertTrue(env.is_linux())
        self.assertFalse(env.is_macos())
        self.assertFalse(env.is_windows())
        self.assertTrue(env.is_presubmit)
        self.assertTrue(env.is_release)
        self.assertEqual(env.dist_dir, pathlib.Path("/tmp/dist"))

    @patch("host.build_environment.platform.system")
    def test_create_build_environment_macos(self, mock_system):
        mock_system.return_value = "Darwin"
        args = argparse.Namespace(
            dist="/tmp/dist", build_id="12345", target="mac_aarch64", config=["ci"]
        )
        env = build_environment.create_build_environment(args)
        self.assertIsInstance(env, build_environment.MacOSBuildEnvironment)
        self.assertTrue(env.is_macos())
        self.assertFalse(env.is_presubmit)
        self.assertFalse(env.is_release)

        with patch(
            "subprocess.check_output", return_value="/Applications/Xcode.app"
        ), env:
            self.assertEqual(
                env.get_env().get("DEVELOPER_DIR"), "/Applications/Xcode.app"
            )

    @patch("host.build_environment.platform.system")
    def test_create_build_environment_windows(self, mock_system):
        mock_system.return_value = "Windows"
        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P999", target="windows_x64", config=["release"]
        )
        env = build_environment.create_build_environment(args)
        self.assertIsInstance(env, build_environment.WindowsBuildEnvironment)
        self.assertTrue(env.is_windows())
        self.assertTrue(env.is_presubmit)

    @patch("host.build_environment.platform.system")
    def test_create_bazel_environment(self, mock_system):
        mock_system.return_value = "Linux"
        args = argparse.Namespace()
        env = build_environment.create_bazel_environment(args)
        self.assertIsInstance(env, build_environment.LinuxBazelEnvironment)
        self.assertTrue(env.is_linux())

    def test_build_environment_dist_dir_fallback(self):
        args = argparse.Namespace(build_id="P100")
        env = build_environment.BuildEnvironment(args)
        self.assertEqual(env.dist_dir, pathlib.Path("PNONE"))

    def test_build_environment_injected_env(self):
        args = argparse.Namespace(dist="/tmp/dist", build_id="P100", config=[])
        custom_env = {
            "BUILD_TARGET_NAME": "custom_target",
            "EMULATOR_SYMBOL_SERVER_KEY": "injected_key_xyz",
            "TMPDIR": "/tmp/custom_tmp",
        }
        env = build_environment.BuildEnvironment(args, env=custom_env)
        self.assertEqual(env.build_target, "custom_target")
        self.assertEqual(env.crashpad_symbol_server_key, "injected_key_xyz")
        self.assertEqual(env.tmp_dir, pathlib.Path("/tmp/custom_tmp"))

    @patch("host.build_environment.asyncio.create_subprocess_exec")
    def test_secret_masking_in_logs(self, mock_create_proc):
        # Setup mock process with proper AsyncMock objects
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stdout.readline = AsyncMock(side_effect=[b"out line\n", b""])
        mock_proc.stderr.readline = AsyncMock(side_effect=[b""])
        mock_create_proc.return_value = mock_proc

        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P123", target="linux_x64", config=[]
        )
        env = build_environment.BuildEnvironment(args)
        env.crashpad_symbol_server_key = "super_secret_crash_key"

        cmd = [
            "sym_upload",
            "-k",
            "key_param",
            "prefix_super_secret_crash_key_suffix",
            "--test_env=GEMINI_API_KEY=secret_gemini_key",
            "normal_arg",
        ]

        with self.assertLogs(level="INFO") as cm:
            env.run(cmd, timeout=10)

        log_output = "\n".join(cm.output)
        self.assertIn("-k ******", log_output)
        self.assertIn("prefix_******_suffix", log_output)
        self.assertIn("--test_env=GEMINI_API_KEY=******", log_output)
        self.assertNotIn("super_secret_crash_key", log_output)
        self.assertNotIn("secret_gemini_key", log_output)

    @patch("host.build_environment.asyncio.create_subprocess_exec")
    def test_secret_masking_in_command_failed_exception(self, mock_create_proc):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait = AsyncMock(return_value=1)
        mock_proc.stdout.readline = AsyncMock(side_effect=[b""])
        mock_proc.stderr.readline = AsyncMock(side_effect=[b"fatal error\n", b""])
        mock_create_proc.return_value = mock_proc

        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P123", target="linux_x64", config=[]
        )
        env = build_environment.BuildEnvironment(args)
        env.crashpad_symbol_server_key = "crashpad_secret_token"
        env.gemini_api_key = "gemini_secret_token"

        cmd = [
            "sym_upload",
            "-k",
            "crashpad_secret_token",
            "--test_env=GEMINI_API_KEY=gemini_secret_token",
        ]

        with self.assertRaises(build_environment.CommandFailedException) as ctx:
            env.run(cmd, timeout=10)

        err_msg = str(ctx.exception)
        self.assertNotIn("crashpad_secret_token", err_msg)
        self.assertNotIn("gemini_secret_token", err_msg)
        self.assertIn("-k ******", err_msg)
        self.assertIn("--test_env=GEMINI_API_KEY=******", err_msg)
        self.assertNotIn("crashpad_secret_token", " ".join(ctx.exception.result.args))
        self.assertNotIn("gemini_secret_token", " ".join(ctx.exception.result.args))

    @patch("host.build_environment.asyncio.wait_for")
    @patch("host.build_environment.asyncio.create_subprocess_exec")
    def test_secret_masking_in_timeout_expired(self, mock_create_proc, mock_wait_for):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_create_proc.return_value = mock_proc
        mock_wait_for.side_effect = asyncio.TimeoutError()

        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P123", target="linux_x64", config=[]
        )
        env = build_environment.BuildEnvironment(args)
        env.gemini_api_key = "timeout_secret_gemini_key"

        cmd = ["bazel", "test", "--test_env=GEMINI_API_KEY=timeout_secret_gemini_key"]
        with self.assertRaises(subprocess.TimeoutExpired) as ctx:
            env.run(cmd, timeout=5)

        self.assertNotIn("timeout_secret_gemini_key", str(ctx.exception))
        self.assertIn("--test_env=GEMINI_API_KEY=******", str(ctx.exception))

    def test_bazel_cmd_test_failure_redacts_gemini_api_key(self):
        from engine import bazel

        args = argparse.Namespace(
            dist="/tmp/dist", build_id="P123", target="linux_x64", config=[]
        )
        env = build_environment.BuildEnvironment(args)
        env.gemini_api_key = "bazel_test_secret_key_xyz"
        env.run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[], returncode=bazel.BuildExitCode.TESTS_FAILED
            )
        )

        bzl = bazel.BazelCmd(env)
        with self.assertRaises(build_environment.CommandFailedException) as ctx:
            bzl.test(["@goldfish//..."])

        self.assertNotIn("bazel_test_secret_key_xyz", str(ctx.exception))
        self.assertIn("--test_env=GEMINI_API_KEY=******", str(ctx.exception))

    def test_global_logger_redacts_registered_secrets_and_patterns(self):
        from services.log_handler import register_secret

        register_secret("registered_runtime_secret_999")
        google_key = "AIza" + "A" * 35

        with self.assertLogs(level="INFO") as cm:
            logger.info(
                "Leaked values: %s, url?key=url_secret_123, Bearer oauth_tok_456, %s",
                "registered_runtime_secret_999",
                google_key,
            )

        log_output = "\n".join(cm.output)
        self.assertNotIn("registered_runtime_secret_999", log_output)
        self.assertNotIn("url_secret_123", log_output)
        self.assertNotIn("oauth_tok_456", log_output)
        self.assertNotIn(google_key, log_output)
        self.assertIn("?key=******", log_output)
        self.assertIn("Bearer ******", log_output)

    @patch("host.host_resources.get_host_memory_metrics")
    @patch("host.host_resources.is_memory_constrained")
    def test_host_memory_delegation(self, mock_is_constrained, mock_get_metrics):
        mock_is_constrained.return_value = True
        mock_get_metrics.return_value = MagicMock(total_gb=16.0)

        args = argparse.Namespace(dist="/tmp/dist", build_id="P123", config=[])
        env = build_environment.BuildEnvironment(args)

        self.assertTrue(env.is_memory_constrained())
        mock_is_constrained.assert_called_once_with(env)

        metrics = env.get_host_memory_metrics()
        self.assertEqual(metrics.total_gb, 16.0)
        mock_get_metrics.assert_called_once_with(env)

    @patch("host.build_environment.platform.system")
    def test_is_cross_compile(self, mock_system):
        mock_system.return_value = "Linux"
        env_native = build_environment.create_build_environment(
            argparse.Namespace(
                dist="/tmp/dist", build_id="P123", target="linux_x64", config=[]
            )
        )
        self.assertFalse(env_native.is_cross_compile())

        env_cross = build_environment.create_build_environment(
            argparse.Namespace(
                dist="/tmp/dist", build_id="P123", target="linux_aarch64", config=[]
            )
        )
        self.assertTrue(env_cross.is_cross_compile())

        env_trusty = build_environment.create_build_environment(
            argparse.Namespace(
                dist="/tmp/dist", build_id="P123", target="trusty_x64", config=[]
            )
        )
        self.assertFalse(env_trusty.is_cross_compile())


if __name__ == "__main__":
    unittest.main()
