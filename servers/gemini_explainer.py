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
import urllib.error

import build_environment
from gemini_client import GeminiClient
from change_info import ChangeInfo


class GeminiExplainer:
    def __init__(
        self,
        args: argparse.Namespace,
    ):
        """Initializes the BazelaEnvironment.

        Args:
            args: Parsed command-line arguments.
        """
        self.args = args
        self._log_handler = None
        self._temp_log_file = None

    def __enter__(self):
        """Configure platform specific settings if needed."""
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
        """Restores platform specific settings if needed."""
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
        except urllib.error.HTTPError as e:
            msg = f"Gemini failed to explain the failure due to: {e.reason}"
            logging.error(msg[:170])
        except Exception as e:
            msg = f"Gemini failed to explain the failure due to: {e}"
            logging.error(msg[:170])

    def explain_failure(self, log: str):
        with build_environment.create_build_environment(self.args) as env:
            change_info = ChangeInfo(self.args.change_info)
            code_changes = change_info.get_all_parent_diffs(env)
            client = GeminiClient()
            gemini_prompt = f"""You are an expert software engineer and technical detective. Your task is to find the **root cause** of a build failure.

You will be provided with:
1.  **Build Logs**: The full output from the failed build process.
2.  **Code Changes**: The `git show` output for the commits included in the build.

Analyze these two pieces of information to determine why the build failed. Then, provide a single, concise paragraph that explains the root cause.
Your explanation should not exceed 20 lines and a line should not contain more than 170 characters.

--- BUILD LOGS ---
{log}
--- END BUILD LOGS ---

--- CODE CHANGES ---
{code_changes}
--- END CODE CHANGES ---"""
            answer = client.get_generated_text(gemini_prompt)
            logging.error(
                "--------------- Gemini will now attempt to explain the build failure:"
            )
            logging.error(textwrap.fill(answer, width=170))
            logging.error("---------------")
