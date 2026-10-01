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
from __future__ import annotations

import logging
import tempfile
import threading
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from engine import bazel
    from host import build_environment
except ImportError:
    import bazel
    import build_environment


class Symuploader:
    """Uploads Breakpad/Crashpad symbol files (.sym) to symbol servers.

    Resolves the `@breakpad//:sym_upload` native binary once and executes uploads
    concurrently across a thread pool without per-file Bazel server overhead.
    """

    SYM_UPLOAD_TARGET = "@breakpad//:sym_upload"
    SUPPORTED_SUFFIXES = (".sym",)
    DEFAULT_MAX_WORKERS = 16

    def __init__(
        self,
        env: build_environment.BuildEnvironment,
        bzl: bazel.BazelCmd,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """Initializes the Symuploader.

        Args:
            env: The build environment.
            bzl: The bazel command wrapper.
            max_workers: Maximum number of concurrent upload worker threads.
        """
        self.env = env
        self.bzl = bzl
        self.max_workers = max_workers
        self._sym_upload_bin: Path | None = None
        self._bin_lock = threading.Lock()

    def _get_sym_upload_binary(self) -> Path:
        """Resolves and caches the native @breakpad//:sym_upload executable path."""
        if self._sym_upload_bin is None:
            with self._bin_lock:
                if self._sym_upload_bin is None:
                    artifacts = self.bzl.query_artifacts([self.SYM_UPLOAD_TARGET])
                    if not artifacts:
                        raise FileNotFoundError(
                            f"Could not resolve executable for {self.SYM_UPLOAD_TARGET}."
                        )
                    bin_path = Path(artifacts[0])
                    if not bin_path.exists():
                        self.bzl.build([self.SYM_UPLOAD_TARGET])
                    self._sym_upload_bin = bin_path
        return self._sym_upload_bin

    def _resolve_servers(
        self, server: str | None = None, servers: Sequence[str] | None = None
    ) -> list[str]:
        if servers:
            return [s for s in servers if s]
        return [server or self.env.crashpad_server]

    def upload_artifact(self, symbol_file: Path, server: str | None = None) -> None:
        """Uploads a single .sym symbol file to the crashpad server.

        Args:
            symbol_file: Path to the .sym file.
            server: The server URL to upload to. Defaults to env.crashpad_server.

        Raises:
            build_environment.CommandFailedException: If the sym_upload command fails (except exit code 2).
        """
        sym_upload_bin = self._get_sym_upload_binary()
        target_server = server or self.env.crashpad_server
        if self.env.is_windows():
            params = [
                "-p",
                str(symbol_file),
                target_server,
                self.env.crashpad_symbol_server_key,
            ]
        else:
            params = [
                "-p",
                "sym-upload-v2",
                "-k",
                self.env.crashpad_symbol_server_key,
                str(symbol_file),
                target_server,
            ]
        try:
            self.env.run([str(sym_upload_bin), *params])
        except build_environment.CommandFailedException as cfe:
            if cfe.result.returncode == 2:
                logger.debug("Symbols already present in the server.")
            else:
                raise

    def _upload_files_parallel(
        self,
        files: Sequence[Path],
        target_servers: Sequence[str],
        ignore_failures: bool = False,
    ) -> tuple[int, int]:
        """Uploads symbol files across one or more target servers concurrently."""
        tasks = [(symbol_file, srv) for srv in target_servers for symbol_file in files]
        if not tasks:
            return 0, 0

        self._get_sym_upload_binary()
        processed_count = 0
        failed_count = 0
        workers = min(self.max_workers, len(tasks))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_task = {
                executor.submit(self.upload_artifact, symbol_file, srv): (
                    symbol_file,
                    srv,
                )
                for symbol_file, srv in tasks
            }
            for future in as_completed(future_to_task):
                symbol_file, srv = future_to_task[future]
                try:
                    future.result()
                    processed_count += 1
                except build_environment.CommandFailedException as cfe:
                    failed_count += 1
                    if not ignore_failures:
                        for pending in future_to_task:
                            pending.cancel()
                        raise
                    logger.warning(
                        "Failed to process %s on %s, due to %s",
                        symbol_file,
                        srv,
                        cfe,
                    )

        return processed_count, failed_count

    def upload_artifacts(
        self,
        artifacts: list[str],
        server: str | None = None,
        servers: Sequence[str] | None = None,
    ) -> None:
        """Uploads multiple symbol files produced by a list of Bazel targets.

        Args:
            artifacts: A list of Bazel targets that produce .sym files.
            server: Single server URL to upload to. Defaults to env.crashpad_server.
            servers: Optional sequence of server URLs to upload to concurrently.
        """
        target_servers = self._resolve_servers(server=server, servers=servers)
        symbol_files = self.bzl.query_artifacts(artifacts)
        self._upload_files_parallel(symbol_files, target_servers)

    def upload_from_zip(
        self,
        zip: Path,
        ignore_failures: bool = False,
        server: str | None = None,
        servers: Sequence[str] | None = None,
    ) -> None:
        """Uploads symbol files (.sym) extracted from a zip archive.

        Extracts only .sym files once and uploads them in parallel across
        all requested Crashpad servers.

        Args:
            zip: Path to the zip archive.
            ignore_failures: If True, failures are logged as warnings instead of raising.
            server: Single server URL to upload to. Defaults to env.crashpad_server.
            servers: Optional sequence of server URLs to upload to in a single extraction pass.

        Raises:
            FileNotFoundError: If the zip file does not exist.
        """
        if not zip.exists():
            raise FileNotFoundError(f"Zip file not found: {zip}")

        target_servers = self._resolve_servers(server=server, servers=servers)
        servers_desc = ", ".join(target_servers)
        logger.info("Uploading symbols from '%s' to %s...", zip.name, servers_desc)

        with tempfile.TemporaryDirectory() as extract_dir_str:
            extract_dir = Path(extract_dir_str)
            with zipfile.ZipFile(zip, "r") as zip_ref:
                symbol_members = [
                    info
                    for info in zip_ref.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in self.SUPPORTED_SUFFIXES
                ]
                zip_ref.extractall(extract_dir, members=symbol_members)
                symbol_files = [extract_dir / info.filename for info in symbol_members]

            processed_count, failed_count = self._upload_files_parallel(
                symbol_files, target_servers, ignore_failures=ignore_failures
            )

        logger.info(
            "Symbol upload complete for '%s' (%s): %d succeeded, %d failed.",
            zip.name,
            servers_desc,
            processed_count,
            failed_count,
        )
