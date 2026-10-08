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

"""AEMU build pipeline, execution engine, host diagnostics, and services."""

import sys
from pathlib import Path

# Ensure this directory is in sys.path so subpackages can be imported seamlessly
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

# Subpackages
from . import engine, gemini, host, pipeline, services

__all__ = [
    "engine",
    "gemini",
    "host",
    "pipeline",
    "services",
]
