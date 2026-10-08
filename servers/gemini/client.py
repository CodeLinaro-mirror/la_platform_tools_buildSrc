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
import urllib.error
import urllib.request

try:
    from .auth import GeminiAuthenticator
except ImportError:
    try:
        from gemini.auth import GeminiAuthenticator
    except ImportError:
        from gemini_auth import GeminiAuthenticator

try:
    from services.log_handler import redact_secrets, register_secret
except ImportError:
    from log_handler import redact_secrets, register_secret


class GeminiClient:
    """A client for the Gemini API."""

    def __init__(
        self,
        model: str = "gemini-flash-latest",
        service_account_email: str = "build-runner@emulator-builds.iam.gserviceaccount.com",
    ):
        self.model = model
        authenticator = GeminiAuthenticator(service_account_email)
        credentials = authenticator.get_credentials(self.model)

        self.api_key = credentials["api_key"]
        if isinstance(self.api_key, str):
            register_secret(self.api_key)
        self.headers = credentials["headers"]
        self.endpoint = credentials["endpoint"]
        self.auth_mode = credentials["auth_mode"]

    def _prepare_request_body(self, prompt: str):
        """Prepares the request body based on the authentication mode."""
        if self.auth_mode in ["API Key", "API Key from Secret"]:
            return {"contents": [{"parts": [{"text": prompt}]}]}
        # GCE Credentials endpoint has a different payload structure
        return {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}

    def generate_content(self, prompt: str):
        """Sends a content generation request to the Gemini API.

        Args:
            prompt: The text prompt for the model.

        Returns:
            dict: The JSON response from the API.
        """
        body = self._prepare_request_body(prompt)
        json_data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint, data=json_data, headers=self.headers, method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                response_body = response.read().decode("utf-8")
                return json.loads(response_body)
        except urllib.error.HTTPError as e:
            extra = [self.api_key] if isinstance(self.api_key, str) else []
            if getattr(e, "url", None):
                e.url = redact_secrets(str(e.url), extra_secrets=extra)
            if getattr(e, "filename", None):
                e.filename = redact_secrets(str(e.filename), extra_secrets=extra)
            if getattr(e, "msg", None):
                e.msg = redact_secrets(str(e.msg), extra_secrets=extra)
            raise
        except urllib.error.URLError as e:
            extra = [self.api_key] if isinstance(self.api_key, str) else []
            if getattr(e, "reason", None) and isinstance(e.reason, str):
                e.reason = redact_secrets(e.reason, extra_secrets=extra)
            if getattr(e, "filename", None):
                e.filename = redact_secrets(str(e.filename), extra_secrets=extra)
            raise

    def parse_response(self, response_json: dict) -> list[str]:
        """Parses a raw JSON response from the Gemini API and extracts the text content.

        Args:
            response_json: The raw JSON dictionary from the API.

        Returns:
            list: A list of strings containing the generated text from all candidates.
        """
        extracted_text = []
        try:
            candidates = response_json.get("candidates", [])
            for candidate in candidates:
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                for part in parts:
                    if "text" in part:
                        extracted_text.append(part["text"])
        except (AttributeError, KeyError) as e:
            raise ValueError("Invalid response JSON format.") from e
        return extracted_text

    def get_generated_text(self, prompt: str) -> str:
        """Sends a prompt to the API and returns only the generated text.

        Args:
            prompt: The text prompt for the model.

        Returns:
            str: The concatenated generated text.
        """
        raw_response = self.generate_content(prompt)
        text_list = self.parse_response(raw_response)
        return "".join(text_list)
