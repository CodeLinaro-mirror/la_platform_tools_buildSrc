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
import base64
import json
import logging
import os
import socket
import urllib.error
import urllib.request


class GeminiAuthenticator:
    """
    Handles authentication for the Gemini API.

    It prioritizes a GEMINI_API_KEY environment variable. If not found, it
    retrieves an API key from Google Secret Manager using the service account
    credentials of the machine it is running on.

    It is expected that the `GCE_METADATA_HOST` is available on the machine if
    an API key is not present.

    We use an API key to ensure we can decouple billing for gemini usage from
    our build environment.
    """

    def __init__(
        self,
        service_account_email="build-runner@emulator-builds.iam.gserviceaccount.com",
    ):
        self.service_account_email = service_account_email

    def get_credentials(self, model):
        """
        Configures authentication by trying different methods in order.
        1. API key from GEMINI_API_KEY environment variable.
        2. API key from Secret Manager using the machine's service account.
        """
        # 1. Try environment variable
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            logging.info("Using API key from GEMINI_API_KEY environment variable.")
            return {
                "api_key": api_key,
                "endpoint": f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                "auth_mode": "API Key",
                "headers": {"Content-Type": "application/json"},
            }

        # 2. Try Secret Manager
        logging.info("Attempting to retrieve API key from Secret Manager.")
        # Try default service account
        access_token = self._get_access_token_for_sa("default")

        if access_token:
            try:
                secret_resource = "projects/885808572500/secrets/gemini_build_api/versions/latest:access"
                secret_url = (
                    f"https://secretmanager.googleapis.com/v1/{secret_resource}"
                )
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }
                req = urllib.request.Request(secret_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    secret_payload = json.loads(response.read().decode())
                    secret_b64 = secret_payload["payload"]["data"]
                    api_key = base64.b64decode(secret_b64).decode("utf-8").strip()

                logging.info(
                    "Successfully configured with API key from Secret Manager."
                )
                return {
                    "api_key": api_key,
                    "auth_mode": "API Key from Secret",
                    "headers": {"Content-Type": "application/json"},
                    "endpoint": f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                }
            except (urllib.error.URLError, ValueError, socket.timeout, KeyError) as e:
                logging.warning(
                    "Failed to retrieve API key from Secret Manager. Please ensure the GCE service account "
                    f"has permissions for Secret Manager. Error: {e}"
                )
        else:
            logging.warning(
                "Cannot access Secret Manager without a GCE access token from the default account."
            )

        raise RuntimeError(
            "Failed to configure Gemini client with any authentication method."
        )

    def _get_access_token_for_sa(self, service_account):
        """Retrieves an access token from the metadata server for the current machine."""
        try:
            metadata_server = "metadata.google.internal"
            if os.getenv("GCE_METADATA_HOST"):
                metadata_server = os.getenv("GCE_METADATA_HOST")
            req = urllib.request.Request(
                f"http://{metadata_server}/computeMetadata/v1/instance/service-accounts/default/token?alt=json",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                token_data = json.loads(response.read().decode())
                access_token = token_data.get("access_token")
                if not access_token:
                    raise ValueError(
                        "Access token not found in metadata server response."
                    )
                logging.info(
                    f"Successfully retrieved GCE access token for {service_account}."
                )
                return access_token
        except (urllib.error.URLError, ValueError, socket.timeout) as e:
            logging.warning(
                f"Failed to get GCE access token for {service_account}: {e}"
            )
            return None
