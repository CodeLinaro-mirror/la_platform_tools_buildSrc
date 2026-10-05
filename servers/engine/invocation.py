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
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from engine import bazel
    from host import build_environment
except ImportError:
    import bazel
    import build_environment

try:
    from unittest.mock import MagicMock
except ImportError:
    MagicMock = None


@dataclass
class BazelInvocation:
    """Encapsulates the configuration and flags for a single Bazel execution."""

    verb: str  # "test", "build", "run", "cquery"
    targets: list[str] = field(default_factory=list)
    startup_options: list[str] = field(default_factory=list)
    build_flags: list[str] = field(default_factory=list)
    invocation_flags: list[str] = field(default_factory=list)
    allow_analysis_cache_discard: bool = True
    allow_no_test: bool = False
    fail_on_test_failure: bool = True
    timeout: float = 7200.0

    def execute(
        self, env: build_environment.BuildEnvironment
    ) -> subprocess.CompletedProcess:
        """Constructs a BazelCmd with configured startup & build flags and runs the verb."""
        bzl = bazel.BazelCmd(
            env, startup_options=self.startup_options
        ).with_build_flags(self.build_flags)

        if self.verb == "test":
            return bzl.test(
                self.targets,
                invocation_flags=self.invocation_flags,
                allow_analysis_cache_discard=self.allow_analysis_cache_discard,
                allow_no_test=self.allow_no_test,
                fail_on_test_failure=self.fail_on_test_failure,
                timeout=self.timeout,
            )
        elif self.verb == "build":
            return bzl.build(
                self.targets,
                invocation_flags=self.invocation_flags,
                allow_analysis_cache_discard=self.allow_analysis_cache_discard,
                timeout=self.timeout,
            )
        else:
            raise ValueError(f"Unsupported Bazel invocation verb: {self.verb}")


class BazelInvocationBuilder:
    """Fluent builder for constructing standardized, deterministic Bazel invocations."""

    def __init__(
        self,
        verb: str,
        env: build_environment.BuildEnvironment,
        startup_options: Iterable[str] = (),
    ):
        self.verb = verb
        self.env = env
        self.startup_options = list(startup_options)
        self.build_flags: list[str] = []
        self.invocation_flags: list[str] = []
        self.targets: list[str] = []
        self.allow_analysis_cache_discard = True
        self.allow_no_test = False
        self.fail_on_test_failure = True
        self.timeout = 3600.0 * 5 if env.is_macos() else 3600.0 * 2

    def with_targets(self, targets: Iterable[str]) -> "BazelInvocationBuilder":
        self.targets.extend(targets)
        return self

    def with_common_flags(
        self, base_build_options: Iterable[str]
    ) -> "BazelInvocationBuilder":
        """Attaches standard build metadata, embed labels, and target configs."""
        flags = list(base_build_options) + [
            f"--build_metadata=ab_build_id={self.env.build_id}",
            f"--build_metadata=ab_target={self.env.build_target}",
            "--verbose_failures",
            "--stamp",
            f"--embed_label={self.env.build_id}",
            f"--@goldfish//emulator:build_id={self.env.build_id}",
            f"--@qemu//google/toolchain:build_id={self.env.build_id}",
            f"--@goldfish//emulator:is_presubmit={str(self.env.is_presubmit).lower()}",
        ]
        if (
            self.env.target_platform.startswith("linux")
            and self.env.is_release
            and not self.env.is_presubmit
        ):
            flags.append("--config=lto")
        self.build_flags.extend(flags)
        return self

    def with_build_flags(self, flags: Iterable[str]) -> "BazelInvocationBuilder":
        self.build_flags.extend(flags)
        return self

    def with_invocation_flags(self, flags: Iterable[str]) -> "BazelInvocationBuilder":
        self.invocation_flags.extend(flags)
        return self

    def with_profile(self, profile_path: Path) -> "BazelInvocationBuilder":
        self.invocation_flags.append(f"--profile={profile_path}")
        return self

    def with_test_defaults(self, test_definition_name: str) -> "BazelInvocationBuilder":
        """Configures standard test flags: Ants config, detailed summary, test definition."""
        self.invocation_flags.extend(
            [
                "--config=ants",
                f"--build_metadata=test_definition_name={test_definition_name}",
                "--test_output=errors",
                "--test_summary=detailed",
                "--test_timeout=1200,1800,2400,3000",
            ]
        )
        return self

    def with_thin_lto_disabled(self) -> "BazelInvocationBuilder":
        """Bypasses LTO compile actions for fast unit test compilation."""
        if "--config=lto" in self.build_flags:
            self.build_flags = [f for f in self.build_flags if f != "--config=lto"]
            self.build_flags.append("--features=-thin_lto")
        return self

    def with_build_tests_only(self) -> "BazelInvocationBuilder":
        self.invocation_flags.append("--build_tests_only")
        return self

    def with_memory_pressure_limits(
        self, constrained: bool | None = None
    ) -> "BazelInvocationBuilder":
        """Applies concurrency and memory bounds if the host is memory-constrained."""
        if constrained is not None:
            is_constrained = constrained
        elif hasattr(self.env, "is_memory_constrained"):
            res = self.env.is_memory_constrained()
            is_constrained = (
                bool(res)
                if not (MagicMock is not None and isinstance(res, MagicMock))
                else self.env.is_macos()
            )
        else:
            is_constrained = self.env.is_macos()

        if is_constrained:
            logger.info(
                "Applying memory-pressure throttling (--jobs=2, --local_resources=memory=HOST_RAM*.3)"
            )
            self.invocation_flags.extend(
                [
                    "--jobs=2",
                    "--local_resources=memory=HOST_RAM*.3",
                ]
            )
        return self

    def with_macos_limits(self) -> "BazelInvocationBuilder":
        """Applies memory and concurrency bounds on macOS runners (delegates to with_memory_pressure_limits)."""
        return self.with_memory_pressure_limits()

    def with_jvm_heap_limit(self, size: str = "4g") -> "BazelInvocationBuilder":
        self.startup_options.append(f"--host_jvm_args=-Xmx{size}")
        return self

    def with_timeout(self, timeout: float) -> "BazelInvocationBuilder":
        self.timeout = timeout
        return self

    def with_fail_on_test_failure(self, fail: bool) -> "BazelInvocationBuilder":
        self.fail_on_test_failure = fail
        return self

    def with_allow_no_test(self, allow: bool) -> "BazelInvocationBuilder":
        self.allow_no_test = allow
        return self

    def build_invocation(self) -> BazelInvocation:
        return BazelInvocation(
            verb=self.verb,
            targets=list(self.targets),
            startup_options=list(self.startup_options),
            build_flags=list(self.build_flags),
            invocation_flags=list(self.invocation_flags),
            allow_analysis_cache_discard=self.allow_analysis_cache_discard,
            allow_no_test=self.allow_no_test,
            fail_on_test_failure=self.fail_on_test_failure,
            timeout=self.timeout,
        )
