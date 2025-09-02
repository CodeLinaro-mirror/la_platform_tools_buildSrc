# Copyright 2025 - The Android Open Source Project
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
import logging
import os
import tempfile
import textwrap

from gemini_client import GeminiClient


class GeminiExplainer:
    def __init__(
        self,
    ):
        """Initializes the BazelaEnvironment.

        Args:
            args: Parsed command-line arguments.
        """
        self._log_handler = None
        self._temp_log_file = None

    def __enter__(self):
        # Add a loghandler to track all the logs.
        self._temp_log_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".log", encoding="utf-8"
        )
        self._log_handler = logging.StreamHandler(self._temp_log_file)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if self._log_handler:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self._log_handler)
            self._log_handler.close()

        if exc_type is not None and self._temp_log_file:
            # An exception has occurred, we will try to explain it.
            self._temp_log_file.seek(0)
            log = self._temp_log_file.read()
            self.safely_explain_failure(log)

        if self._temp_log_file:
            self._temp_log_file.close()
            os.remove(self._temp_log_file.name)

        # Returning None (or False) will re-raise the exception if one occurred.

    def safely_explain_failure(self, log: str):
        try:
            self.explain_failure(log)
        except Exception as e:
            logging.error("Gemini failed to explain the failure due to: %s", e)

    def explain_failure(self, log: str):
        client = GeminiClient()
        gemini_prompt = f"""As an expert software engineer and technical detective, your mission is to solve a build failure.

You will receive the full **build logs** that were part of the build. Your task is to analyze this information to identify the root cause.

Your final output must be a concise, one-paragraph explanation of the problem, limited to a maximum of 20 lines with 170 characters
The explanation should be clear and technical, allowing another engineer to understand the issue quickly.

--- BUILD LOGS ---
{log}
--- END BUILD LOGS ---"""
        answer = client.get_generated_text(gemini_prompt)
        logging.error(
            "--------------- Gemini will now attempt to explain the build failure:"
        )
        logging.error(textwrap.fill(answer, width=170))
        logging.error("---------------")
