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
import logging
import os
import tempfile
import textwrap

logger = logging.getLogger(__name__)

try:
    from gemini.client import GeminiClient
    from host import build_environment
    from services.change_info import ChangeInfo
    from services.log_handler import SecretRedactingFilter, redact_secrets
except ImportError:
    import build_environment

    try:
        from gemini import GeminiClient
    except ImportError:
        from gemini_client import GeminiClient
    try:
        from services import ChangeInfo
    except ImportError:
        from change_info import ChangeInfo
    from log_handler import SecretRedactingFilter, redact_secrets


class GeminiExplainer:
    """Build execution context manager that uses Gemini to explain failures."""

    def __init__(
        self,
        args: argparse.Namespace,
        env: "build_environment.BuildEnvironment" = None,
    ):
        """Initializes the GeminiExplainer.

        Args:
            args: Parsed command-line arguments.
            env: Optional active BuildEnvironment to reuse during failure analysis.
        """
        self.args = args
        self.env = env
        self._log_handler = None
        self._temp_log_file = None

    def __enter__(self):
        """Configure log capture for the build execution."""
        self._temp_log_file = tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".log", encoding="utf-8"
        )
        self._log_handler = logging.StreamHandler(self._temp_log_file)
        self._log_handler.addFilter(SecretRedactingFilter())
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        """Restores log settings and explains failure if an exception occurred."""
        if self._log_handler:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self._log_handler)
            self._log_handler.close()

        if exc_type is not None and self._temp_log_file:
            # An exception has occurred, we will try to explain it.
            self._temp_log_file.seek(0)
            lines = self._temp_log_file.readlines()
            log = self._get_truncated_log(lines)
            self.safely_explain_failure(log)

        if self._temp_log_file:
            self._temp_log_file.close()
            os.remove(self._temp_log_file.name)

        # Returning None (or False) will re-raise the exception if one occurred.

    def _get_truncated_log(
        self, lines: list, header_lines: int = 20, tail_lines: int = 2000
    ) -> str:
        """Truncates the log to include only header and tail lines to reduce token consumption.

        Args:
            lines: List of log lines.
            header_lines: Number of lines to keep from the beginning.
            tail_lines: Number of lines to keep from the end.

        Returns:
            The truncated log string.
        """
        if len(lines) > header_lines + tail_lines:
            return (
                "".join(lines[:header_lines])
                + f"\n\n... [TRUNCATED {len(lines) - header_lines - tail_lines} LINES] ...\n\n"
                + "".join(lines[-tail_lines:])
            )
        return "".join(lines)

    def safely_explain_failure(self, log: str):
        """Invokes explain_failure catching any exceptions to avoid masking the build error."""
        try:
            self.explain_failure(log)
        except Exception as e:  # noqa: BLE001  # Broad catch to avoid masking the build error
            raw_msg = str(e).splitlines()[0] if str(e) else repr(e)
            first_line = redact_secrets(raw_msg)
            logger.error(
                f"Gemini failed to explain the failure due to: {first_line}"[:170]
            )

    def _run_explanation_with_env(self, env, log: str):
        change_info = ChangeInfo(getattr(self.args, "change_info", None))
        code_changes = redact_secrets(str(change_info.get_all_parent_diffs(env)))
        sanitized_log = redact_secrets(log)
        client = GeminiClient()
        gemini_prompt = f"""You are an expert software engineer and technical detective. Your task is to find the **root cause** of a build failure.

You will be provided with:
1.  **Build Logs**: The full output from the failed build process.
2.  **Code Changes**: The `git show` output for the commits included in the build.

Analyze these two pieces of information to determine why the build failed. Then, provide a single, concise paragraph that explains the root cause.
Your explanation should not exceed 20 lines and a line should not contain more than 170 characters.

--- BUILD LOGS ---
{sanitized_log}
--- END BUILD LOGS ---

--- CODE CHANGES ---
{code_changes}
--- END CODE CHANGES ---"""
        answer = client.get_generated_text(gemini_prompt)
        logger.error(
            "--------------- Gemini will now attempt to explain the build failure:"
        )
        logger.error(textwrap.fill(answer, width=170))
        logger.error("---------------")

    def explain_failure(self, log: str):
        """Extracts diffs and logs, prompts Gemini, and prints root cause analysis."""
        if self.env is not None:
            self._run_explanation_with_env(self.env, log)
        else:
            with build_environment.create_build_environment(self.args) as env:
                self._run_explanation_with_env(env, log)
