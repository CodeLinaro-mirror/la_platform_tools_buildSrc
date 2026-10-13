# Copyright 2022 - The Android Open Source Project
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
import logging
import platform
import re
import socket
import threading
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

_SECRETS_LOCK = threading.Lock()
_REGISTERED_SECRETS: set[str] = set()
_MIN_SECRET_LENGTH = 4
_MASK = "******"

_ENV_KEY_PATTERN = re.compile(
    r"((?:GEMINI_API_KEY|EMULATOR_SYMBOL_SERVER_KEY)=)[^\s'\"&]+"
)
_URL_KEY_PATTERN = re.compile(r"([?&]key=)[^&\s'\"]+")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[^\s'\"]+", re.IGNORECASE)
_GOOGLE_API_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
_SYM_UPLOAD_FLAG_PATTERN = re.compile(
    r"((?:sym_upload|sym-upload-v2)\b[^\n]*?\s-k\s+)(?!\*{6})\S+"
)

_LOG_RECORD_REDACTOR_INSTALLED = False


def register_secret(secret: str | None) -> None:
    """Registers a runtime secret string to be globally redacted from all logs."""
    if not secret or not isinstance(secret, str):
        return
    cleaned = secret.strip()
    if len(cleaned) < _MIN_SECRET_LENGTH or cleaned == _MASK:
        return
    with _SECRETS_LOCK:
        _REGISTERED_SECRETS.add(cleaned)
        if secret != cleaned:
            _REGISTERED_SECRETS.add(secret)


def clear_registered_secrets() -> None:
    """Clears registered runtime secrets (primarily for test isolation)."""
    with _SECRETS_LOCK:
        _REGISTERED_SECRETS.clear()


def redact_secrets(text: str, extra_secrets: Iterable[str | None] = ()) -> str:
    """Redacts registered secrets and known API key patterns from text."""
    if not text or not isinstance(text, str):
        return text

    with _SECRETS_LOCK:
        secrets = set(_REGISTERED_SECRETS)
    for s in extra_secrets:
        if isinstance(s, str):
            cleaned = s.strip()
            if len(cleaned) >= _MIN_SECRET_LENGTH and cleaned != _MASK:
                secrets.add(cleaned)
                if s != cleaned:
                    secrets.add(s)

    for secret in sorted(secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, _MASK)

    text = _ENV_KEY_PATTERN.sub(rf"\g<1>{_MASK}", text)
    text = _URL_KEY_PATTERN.sub(rf"\g<1>{_MASK}", text)
    text = _BEARER_PATTERN.sub(rf"\g<1>{_MASK}", text)
    text = _GOOGLE_API_KEY_PATTERN.sub(_MASK, text)
    text = _SYM_UPLOAD_FLAG_PATTERN.sub(rf"\g<1>{_MASK}", text)
    return text


def mask_command(
    cmd: Iterable[Any], extra_secrets: Iterable[str | None] = ()
) -> list[str]:
    """Returns a stringified command list with secret flags and values masked."""
    cmd_str = [str(x) for x in cmd]
    masked: list[str] = []
    mask_next = False
    secrets_list = [s for s in extra_secrets if isinstance(s, str) and s.strip()]
    for s in secrets_list:
        register_secret(s)

    for arg in cmd_str:
        if mask_next:
            masked.append(_MASK)
            mask_next = False
        elif arg == "-k":
            masked.append(arg)
            mask_next = True
        else:
            masked.append(redact_secrets(arg, extra_secrets=secrets_list))
    return masked


class SecretRedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from log messages and exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "_secret_redactor_wrapped", False):
            orig_get_message = record.getMessage

            def _redacted_get_message() -> str:
                return redact_secrets(orig_get_message())

            record.getMessage = _redacted_get_message
            record._secret_redactor_wrapped = True
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


def install_log_record_redactor() -> None:
    """Installs a global LogRecord factory that redacts secrets from getMessage()."""
    global _LOG_RECORD_REDACTOR_INSTALLED
    if _LOG_RECORD_REDACTOR_INSTALLED:
        return
    orig_factory = logging.getLogRecordFactory()

    def _redacting_record_factory(*args, **kwargs) -> logging.LogRecord:
        record = orig_factory(*args, **kwargs)
        orig_get_message = record.getMessage

        def _redacted_get_message() -> str:
            return redact_secrets(orig_get_message())

        record.getMessage = _redacted_get_message
        record._secret_redactor_wrapped = True
        return record

    logging.setLogRecordFactory(_redacting_record_factory)
    _LOG_RECORD_REDACTOR_INSTALLED = True


install_log_record_redactor()


def get_host_and_ip():
    """Try to get the hostname and IP address.

    Returns:
        tuple: A tuple containing the hostname and the IP address.
    """
    st = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    my_ip = "127.0.0.1"
    try:
        st.connect(("10.255.255.255", 1))
        my_ip = st.getsockname()[0]
    except OSError as e:
        logger.error("Socket error: %s", e)
    finally:
        st.close()

    hostname = "Unknown"
    try:
        hostname = socket.gethostname()
    except OSError as e:
        # Catch specific OSError for socket gethostname error
        logger.error("Hostname error: %s", e)

    return hostname, my_ip


def log_system_info():
    """Log useful system information."""
    hostname, my_ip = get_host_and_ip()

    logger.info(
        "Hello from %s (%s). I'm a %s build bot", hostname, my_ip, platform.system()
    )
    logger.info("My uname is: %s", platform.uname())


def config_logging():
    """Configure logging format and level and log system information."""
    install_log_record_redactor()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO,
        datefmt="%H:%M:%S",
    )
    root_logger = logging.getLogger()
    redacting_filter = SecretRedactingFilter()
    root_logger.addFilter(redacting_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redacting_filter)
    log_system_info()
