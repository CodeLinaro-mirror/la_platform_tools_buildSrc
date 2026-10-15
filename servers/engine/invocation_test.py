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

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from engine.invocation import BazelInvocation, BazelInvocationBuilder
from host import build_environment


class InvocationTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock(spec=build_environment.BuildEnvironment)
        self.mock_env.build_id = "P12345"
        self.mock_env.build_target = "linux_x64"
        self.mock_env.target_platform = "linux_x64"
        self.mock_env.is_presubmit = True
        self.mock_env.is_release = True
        self.mock_env.is_macos.return_value = False

    def test_builder_common_flags(self):
        builder = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_targets(["@goldfish//emulator:release"])
            .with_common_flags(["--config=ci"])
        )
        inv = builder.build_invocation()
        self.assertEqual(inv.verb, "test")
        self.assertEqual(inv.targets, ["@goldfish//emulator:release"])
        self.assertIn("--config=ci", inv.build_flags)
        self.assertIn("--build_metadata=ab_build_id=P12345", inv.build_flags)
        self.assertNotIn("--config=lto", inv.build_flags)

        # Postsubmit release on Linux adds --config=lto
        self.mock_env.is_presubmit = False
        self.mock_env.is_release = True
        inv_postsubmit = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_common_flags(["--config=ci"])
            .build_invocation()
        )
        self.assertIn("--config=lto", inv_postsubmit.build_flags)

        # Postsubmit debug on Linux omits --config=lto
        self.mock_env.is_release = False
        inv_debug = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_common_flags(["--config=ci"])
            .build_invocation()
        )
        self.assertNotIn("--config=lto", inv_debug.build_flags)

    def test_builder_test_defaults(self):
        builder = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_test_defaults("android_emulator/release")
            .with_profile(Path("/tmp/profile.gz"))
            .with_build_tests_only()
        )
        inv = builder.build_invocation()
        self.assertIn("--config=ants", inv.invocation_flags)
        self.assertIn(
            "--build_metadata=test_definition_name=android_emulator/release",
            inv.invocation_flags,
        )
        self.assertIn("--test_timeout=1200,1800,2400,3000", inv.invocation_flags)
        self.assertIn("--profile=/tmp/profile.gz", inv.invocation_flags)
        self.assertIn("--build_tests_only", inv.invocation_flags)

        unit_inv = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_test_defaults(
                "android_emulator/unit_tests",
                test_timeout="120,300,600,1200",
            )
            .build_invocation()
        )
        self.assertIn("--test_timeout=120,300,600,1200", unit_inv.invocation_flags)

    def test_builder_thin_lto_disabled(self):
        # Presubmit has no --config=lto, so --features must not be mutated (preserving analysis cache)
        self.mock_env.is_presubmit = True
        self.mock_env.is_release = True
        presubmit_inv = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_common_flags(["--config=release"])
            .with_thin_lto_disabled()
            .build_invocation()
        )
        self.assertNotIn("--features=-thin_lto", presubmit_inv.build_flags)
        self.assertNotIn("--config=lto", presubmit_inv.build_flags)

        # Postsubmit Linux release has --config=lto, so it is stripped and --features=-thin_lto added
        self.mock_env.is_presubmit = False
        self.mock_env.is_release = True
        builder = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_common_flags(["--config=release"])
            .with_thin_lto_disabled()
        )
        inv = builder.build_invocation()
        self.assertIn("--features=-thin_lto", inv.build_flags)
        self.assertNotIn("--config=lto", inv.build_flags)
        self.assertIn("--stamp", inv.build_flags)
        self.assertIn(f"--embed_label={self.mock_env.build_id}", inv.build_flags)

    def test_builder_macos_limits(self):
        self.mock_env.is_macos.return_value = True
        builder = (
            BazelInvocationBuilder("test", self.mock_env)
            .with_macos_limits()
            .with_jvm_heap_limit("2g")
        )
        inv = builder.build_invocation()
        self.assertIn("--jobs=2", inv.invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", inv.invocation_flags)
        self.assertIn("--host_jvm_args=-Xmx2g", inv.startup_options)

    def test_builder_macos_limits_skipped_on_linux(self):
        self.mock_env.is_macos.return_value = False
        builder = BazelInvocationBuilder("test", self.mock_env).with_macos_limits()
        inv = builder.build_invocation()
        self.assertNotIn("--jobs=2", inv.invocation_flags)

    def test_builder_memory_pressure_limits_linux_constrained(self):
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = True
        builder = BazelInvocationBuilder(
            "test", self.mock_env
        ).with_memory_pressure_limits()
        inv = builder.build_invocation()
        self.assertIn("--jobs=2", inv.invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", inv.invocation_flags)

    def test_builder_memory_pressure_limits_linux_unconstrained(self):
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = False
        builder = BazelInvocationBuilder(
            "test", self.mock_env
        ).with_memory_pressure_limits()
        inv = builder.build_invocation()
        self.assertNotIn("--jobs=2", inv.invocation_flags)
        self.assertNotIn("--local_resources=memory=HOST_RAM*.3", inv.invocation_flags)

    def test_builder_memory_pressure_limits_windows_constrained(self):
        self.mock_env.is_windows.return_value = True
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_memory_constrained.return_value = True
        builder = BazelInvocationBuilder(
            "test", self.mock_env
        ).with_memory_pressure_limits()
        inv = builder.build_invocation()
        self.assertIn("--jobs=2", inv.invocation_flags)
        self.assertIn("--local_resources=memory=HOST_RAM*.3", inv.invocation_flags)

    def test_builder_memory_pressure_limits_explicit_override(self):
        self.mock_env.is_memory_constrained.return_value = False
        builder = BazelInvocationBuilder(
            "test", self.mock_env
        ).with_memory_pressure_limits(constrained=True)
        inv = builder.build_invocation()
        self.assertIn("--jobs=2", inv.invocation_flags)

        self.mock_env.is_memory_constrained.return_value = True
        builder2 = BazelInvocationBuilder(
            "test", self.mock_env
        ).with_memory_pressure_limits(constrained=False)
        inv2 = builder2.build_invocation()
        self.assertNotIn("--jobs=2", inv2.invocation_flags)

    @patch("engine.invocation.bazel.BazelCmd")
    def test_execute_test_verb(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl

        inv = BazelInvocation(
            verb="test",
            targets=["@goldfish//..."],
            startup_options=["--startup=1"],
            build_flags=["--build=1"],
            invocation_flags=["--inv=1"],
            timeout=1800.0,
        )
        inv.execute(self.mock_env)

        mock_bazel_cmd_cls.assert_called_once_with(
            self.mock_env, startup_options=["--startup=1"]
        )
        mock_bazel_cmd_cls.return_value.with_build_flags.assert_called_once_with(
            ["--build=1"]
        )
        mock_bzl.test.assert_called_once_with(
            ["@goldfish//..."],
            invocation_flags=["--inv=1"],
            allow_analysis_cache_discard=True,
            allow_no_test=False,
            fail_on_test_failure=True,
            timeout=1800.0,
        )

    @patch("engine.invocation.bazel.BazelCmd")
    def test_execute_coverage_verb(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl

        inv = BazelInvocation(
            verb="coverage",
            targets=["@goldfish//..."],
            startup_options=["--startup=1"],
            build_flags=["--build=1"],
            invocation_flags=["--inv=1"],
            timeout=1800.0,
        )
        inv.execute(self.mock_env)

        mock_bazel_cmd_cls.assert_called_once_with(
            self.mock_env, startup_options=["--startup=1"]
        )
        mock_bazel_cmd_cls.return_value.with_build_flags.assert_called_once_with(
            ["--build=1"]
        )
        mock_bzl.coverage.assert_called_once_with(
            ["@goldfish//..."],
            invocation_flags=["--inv=1"],
            allow_analysis_cache_discard=True,
            allow_no_test=False,
            fail_on_test_failure=True,
            timeout=1800.0,
        )

    @patch("engine.invocation.bazel.BazelCmd")
    def test_execute_build_verb(self, mock_bazel_cmd_cls):
        mock_bzl = MagicMock()
        mock_bazel_cmd_cls.return_value.with_build_flags.return_value = mock_bzl

        inv = BazelInvocation(
            verb="build",
            targets=["@goldfish//emulator:release"],
            invocation_flags=["--profile=p.gz"],
        )
        inv.execute(self.mock_env)

        mock_bzl.build.assert_called_once_with(
            ["@goldfish//emulator:release"],
            invocation_flags=["--profile=p.gz"],
            allow_analysis_cache_discard=True,
            timeout=7200.0,
        )


if __name__ == "__main__":
    unittest.main()
