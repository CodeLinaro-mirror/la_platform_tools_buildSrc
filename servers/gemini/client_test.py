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

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from gemini import client


class GeminiClientTest(unittest.TestCase):

    @patch("gemini.client.GeminiAuthenticator")
    def setUp(self, mock_auth_cls):
        self.mock_auth = MagicMock()
        self.mock_auth.get_credentials.return_value = {
            "api_key": "test_key_123",
            "headers": {"Content-Type": "application/json"},
            "endpoint": "https://api.example.com/v1/models/gemini:generateContent?key=test_key_123",
            "auth_mode": "API Key",
        }
        mock_auth_cls.return_value = self.mock_auth
        self.client = client.GeminiClient()

    def test_init_configures_from_credentials(self):
        self.assertEqual(self.client.api_key, "test_key_123")
        self.assertEqual(self.client.auth_mode, "API Key")
        self.assertEqual(self.client.headers, {"Content-Type": "application/json"})

    def test_prepare_request_body_api_key_mode(self):
        body = self.client._prepare_request_body("test prompt")
        self.assertEqual(body, {"contents": [{"parts": [{"text": "test prompt"}]}]})

    def test_prepare_request_body_gce_mode(self):
        self.client.auth_mode = "GCE Credentials"
        body = self.client._prepare_request_body("test prompt")
        self.assertEqual(
            body, {"contents": [{"role": "user", "parts": [{"text": "test prompt"}]}]}
        )

    @patch("urllib.request.urlopen")
    def test_generate_content(self, mock_urlopen):
        expected_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Build failed due to missing symbol."}]
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(expected_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.client.generate_content("why did it fail?")
        self.assertEqual(result, expected_response)

    def test_parse_response_valid(self):
        response_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Part 1. "},
                            {"text": "Part 2."},
                        ]
                    }
                }
            ]
        }
        text_list = self.client.parse_response(response_json)
        self.assertEqual(text_list, ["Part 1. ", "Part 2."])

    def test_parse_response_invalid(self):
        with self.assertRaises(ValueError):
            self.client.parse_response("not a dict")

    @patch.object(client.GeminiClient, "generate_content")
    def test_get_generated_text(self, mock_generate):
        mock_generate.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Failure explanation."},
                        ]
                    }
                }
            ]
        }
        text = self.client.get_generated_text("explain")
        self.assertEqual(text, "Failure explanation.")

    @patch("urllib.request.urlopen")
    def test_generate_content_http_error_scrubs_api_key(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url=self.client.endpoint,
            code=403,
            msg="Forbidden for key=test_key_123",
            hdrs=None,
            fp=None,
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.client.generate_content("prompt")

        self.assertNotIn("test_key_123", str(ctx.exception))
        self.assertNotIn("test_key_123", ctx.exception.url)
        self.assertIn("******", ctx.exception.url)

    def test_explainer_safely_explain_failure_redacts_secrets(self):
        import argparse

        from gemini.explainer import GeminiExplainer

        explainer = GeminiExplainer(argparse.Namespace(dist="/tmp/dist"))
        with (
            patch.object(
                explainer,
                "explain_failure",
                side_effect=RuntimeError(
                    "Connection failed to https://generativelanguage.googleapis.com?key=secret_explainer_key"
                ),
            ),
            self.assertLogs(level="ERROR") as cm,
        ):
            explainer.safely_explain_failure("some log")

        log_output = "\n".join(cm.output)
        self.assertNotIn("secret_explainer_key", log_output)
        self.assertIn("?key=******", log_output)


if __name__ == "__main__":
    unittest.main()
