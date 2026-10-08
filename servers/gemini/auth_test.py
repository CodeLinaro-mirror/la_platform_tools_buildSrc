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

import base64
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from gemini import auth


class GeminiAuthenticatorTest(unittest.TestCase):

    def setUp(self):
        self.auth = auth.GeminiAuthenticator()

    def test_get_credentials_from_env_var(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_env_key"}):
            creds = self.auth.get_credentials("gemini-test-model")
            self.assertEqual(creds["api_key"], "test_env_key")
            self.assertEqual(creds["auth_mode"], "API Key")
            self.assertIn("key=test_env_key", creds["endpoint"])
            self.assertIn("gemini-test-model", creds["endpoint"])

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(auth.GeminiAuthenticator, "_get_access_token_for_sa")
    @patch("urllib.request.urlopen")
    def test_get_credentials_from_secret_manager(self, mock_urlopen, mock_token):
        mock_token.return_value = "fake_access_token"

        secret_val = "secret_api_key_123"
        b64_val = base64.b64encode(secret_val.encode("utf-8")).decode("utf-8")
        secret_json = json.dumps({"payload": {"data": b64_val}}).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = secret_json
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        creds = self.auth.get_credentials("gemini-test-model")
        self.assertEqual(creds["api_key"], secret_val)
        self.assertEqual(creds["auth_mode"], "API Key from Secret")
        self.assertIn(f"key={secret_val}", creds["endpoint"])

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(auth.GeminiAuthenticator, "_get_access_token_for_sa")
    def test_get_credentials_fails_when_no_token(self, mock_token):
        mock_token.return_value = None
        with self.assertRaises(RuntimeError) as ctx:
            self.auth.get_credentials("gemini-test-model")
        self.assertIn("Failed to configure Gemini client", str(ctx.exception))

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(auth.GeminiAuthenticator, "_get_access_token_for_sa")
    @patch("urllib.request.urlopen")
    def test_get_credentials_fails_on_secret_manager_error(
        self, mock_urlopen, mock_token
    ):
        mock_token.return_value = "fake_access_token"
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(RuntimeError) as ctx:
            self.auth.get_credentials("gemini-test-model")
        self.assertIn(
            "Failed to retrieve API key from Secret Manager", str(ctx.exception)
        )

    @patch("urllib.request.urlopen")
    def test_get_access_token_for_sa_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"access_token": "token_abc"}).encode(
            "utf-8"
        )
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        token = self.auth._get_access_token_for_sa("default")
        self.assertEqual(token, "token_abc")

    @patch("urllib.request.urlopen")
    def test_get_access_token_for_sa_failure_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Timeout")
        token = self.auth._get_access_token_for_sa("default")
        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
