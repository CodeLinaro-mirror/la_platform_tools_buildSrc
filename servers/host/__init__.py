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

"""Host environments and system resource diagnostics."""

from .build_environment import (
    BuildEnvironment,
    CommandFailedException,
    LinuxBazelEnvironment,
    LinuxBuildEnvironment,
    MacOSBazelEnvironment,
    MacOSBuildEnvironment,
    WindowsBazelEnvironment,
    WindowsBuildEnvironment,
    create_bazel_environment,
    create_build_environment,
    default_target,
)
from .host_resources import (
    HostMemoryMetrics,
    get_host_memory_metrics,
    is_memory_constrained,
    log_host_memory_status,
)

__all__ = [
    "BuildEnvironment",
    "CommandFailedException",
    "HostMemoryMetrics",
    "LinuxBazelEnvironment",
    "LinuxBuildEnvironment",
    "MacOSBazelEnvironment",
    "MacOSBuildEnvironment",
    "WindowsBazelEnvironment",
    "WindowsBuildEnvironment",
    "create_bazel_environment",
    "create_build_environment",
    "default_target",
    "get_host_memory_metrics",
    "is_memory_constrained",
    "log_host_memory_status",
]
