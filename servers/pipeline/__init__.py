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

"""Modular pipeline execution framework, context, and build stages."""

from .context import BuildContext, copy_all
from .pipeline import Pipeline
from .stage import Stage
from .stages import (
    AospStage,
    ClangTidyStage,
    EtsIntegrationStage,
    ReleasePackageStage,
    SymbolUploadStage,
    UnitTestStage,
    _log_host_memory_status,
    log_host_memory_status,
    reclaim_and_stabilize_resources,
)

__all__ = [
    "AospStage",
    "BuildContext",
    "ClangTidyStage",
    "EtsIntegrationStage",
    "Pipeline",
    "ReleasePackageStage",
    "Stage",
    "SymbolUploadStage",
    "UnitTestStage",
    "_log_host_memory_status",
    "copy_all",
    "log_host_memory_status",
    "reclaim_and_stabilize_resources",
]
