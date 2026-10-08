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
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from gemini import explainer


class GeminiExplainerTest(unittest.TestCase):

    def setUp(self):
        self.args = argparse.Namespace(change_info=None, dist="/tmp/dist")
        self.explainer = explainer.GeminiExplainer(self.args)

    def test_get_truncated_log_no_truncation_when_short(self):
        short_lines = [f"line {i}\n" for i in range(10)]
        result = self.explainer._get_truncated_log(
            short_lines, header_lines=5, tail_lines=10
        )
        self.assertEqual(result, "".join(short_lines))

    def test_get_truncated_log_truncates_when_exceeding_limits(self):
        lines = [f"line {i}\n" for i in range(100)]
        result = self.explainer._get_truncated_log(lines, header_lines=5, tail_lines=10)
        self.assertIn("line 0\n", result)
        self.assertIn("line 4\n", result)
        self.assertIn("line 99\n", result)
        self.assertIn("TRUNCATED 85 LINES", result)

    def test_context_manager_normal_execution(self):
        temp_file_name = None
        with self.explainer as exp:
            self.assertIsNotNone(exp._temp_log_file)
            temp_file_name = exp._temp_log_file.name
            self.assertTrue(os.path.exists(temp_file_name))

        # After exiting without exception, temp file must be cleaned up
        self.assertFalse(os.path.exists(temp_file_name))

    @patch.object(explainer.GeminiExplainer, "safely_explain_failure")
    def test_context_manager_on_exception(self, mock_safely_explain):
        temp_file_name = None
        with self.assertRaises(ValueError), self.explainer as exp:
            temp_file_name = exp._temp_log_file.name
            raise ValueError("Simulated build error")

        mock_safely_explain.assert_called_once()
        self.assertFalse(os.path.exists(temp_file_name))

    @patch.object(explainer.GeminiExplainer, "explain_failure")
    def test_safely_explain_failure_catches_errors(self, mock_explain):
        mock_explain.side_effect = RuntimeError("Gemini API connection error")
        with self.assertLogs(level="ERROR") as cm:
            self.explainer.safely_explain_failure("sample log")
        self.assertTrue(
            any("Gemini failed to explain the failure" in line for line in cm.output)
        )

    @patch("gemini.explainer.GeminiClient")
    @patch("gemini.explainer.build_environment.create_build_environment")
    def test_explain_failure_reuses_provided_env_and_logs_explanation(
        self, mock_create_env, mock_client_cls
    ):
        mock_env = MagicMock()
        mock_client = MagicMock()
        mock_client.get_generated_text.return_value = "Root cause: missing header foo.h"
        mock_client_cls.return_value = mock_client

        self.explainer.env = mock_env
        with self.assertLogs(level="ERROR") as cm:
            self.explainer.explain_failure("error: foo.h: No such file or directory")

        mock_create_env.assert_not_called()
        log_output = "\n".join(cm.output)
        self.assertIn(
            "Gemini will now attempt to explain the build failure", log_output
        )
        self.assertIn("Root cause: missing header foo.h", log_output)


if __name__ == "__main__":
    unittest.main()
