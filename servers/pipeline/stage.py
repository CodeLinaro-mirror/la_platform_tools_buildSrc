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

from abc import ABC, abstractmethod

try:
    from pipeline.context import BuildContext
except ImportError:
    from context import BuildContext


class Stage(ABC):
    """Abstract base class representing an atomic phase in the build & test lifecycle."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the stage for telemetry, metrics, and logs."""

    def should_run(self, ctx: BuildContext) -> bool:
        """Determines if this stage is active under current arguments and environment."""
        return True

    def pre_execute(self, ctx: BuildContext) -> None:
        """Lifecycle hook executed immediately prior to stage execution."""

    @abstractmethod
    def execute(self, ctx: BuildContext) -> None:
        """Executes the stage actions (Bazel invocations, queries, packaging)."""

    def on_failure(self, ctx: BuildContext, exc: Exception) -> None:
        """Hook called when stage execution encounters an error or timeout."""
