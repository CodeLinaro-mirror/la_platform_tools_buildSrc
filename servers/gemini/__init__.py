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

"""Gemini AI failure analysis and credentials package."""

# Compatibility aliases
from . import auth as gemini_auth
from . import client as gemini_client
from . import explainer as gemini_explainer
from .auth import GeminiAuthenticator
from .client import GeminiClient
from .explainer import GeminiExplainer

__all__ = [
    "GeminiAuthenticator",
    "GeminiClient",
    "GeminiExplainer",
    "auth",
    "client",
    "explainer",
    "gemini_auth",
    "gemini_client",
    "gemini_explainer",
]
