#!/usr/bin/env python3
#
# Copyright 2025 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help="License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help="AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import tempfile
from typing import List
import zipfile
from pathlib import Path

import bazel
import build_environment


class Symuploader:
    """
    A class for uploading symbol files (.sym or .pdb) to a crashpad server.
    """

    def __init__(
        self, env: build_environment.BuildEnvironment, bzl: bazel.BazelCmd
    ) -> None:
        """
        Initializes the Symuploader.

        Args:
            env: The build environment.
            bzl: The bazel command wrapper.
        """
        self.env = env
        self.bzl = bzl

    def upload_artifact(self, symbol_or_exe: Path, server: str = None) -> None:
        """
        Uploads a single symbol or windows exectuable file to the crashpad server.

        Args:
            symbol_or_exe: Path to the .sym or .exe file.
            server: The server to upload to. Defaults to the one in the environment.

        Raises:
            build_environment.CommandFailedException: If the sym_upload command fails (except for exit code 2).
        """
        server = server or self.env.crashpad_server
        if self.env.is_windows():
            params = [
                "-p",
                str(symbol_or_exe),
                server,
                self.env.crashpad_symbol_server_key,
            ]
        else:
            params = [
                "-p",
                "sym-upload-v2",
                "-k",
                self.env.crashpad_symbol_server_key,
                str(symbol_or_exe),
                server,
            ]
        try:
            self.bzl.run(
                target="@breakpad//:sym_upload",
                params=params,
            )
        except build_environment.CommandFailedException as cfe:
            if cfe.result.returncode == 2:
                logging.debug("Symbols already present in the server.")
            else:
                raise

    def upload_artifacts(self, artifacts: List[str], server: str = None) -> None:
        """
        Uploads multiple symbol files from a list of bazel targets.

        Args:
            artifacts: A list of bazel targets that produce .sym or .exe files.
            server: The server to upload to. Defaults to the one in the environment.
        """
        for symbol_or_exe in self.bzl.query_artifacts(artifacts):
            self.upload_artifact(symbol_or_exe, server=server)

    def upload_from_zip(
        self, zip: Path, ignore_failures: bool = False, server: str = None
    ) -> None:
        """
        Uploads symbol files (.sym or .exe/.dll) extracted from a zip archive.

        Args:
            zip: Path to the zip file.
            ignore_failures: If True, failures will be logged as warnings instead of raising an exception.
            server: The server to upload to. Defaults to the one in the environment.

        Raises:
            FileNotFoundError: If the zip file does not exist.
        """
        if not zip.exists():
            raise FileNotFoundError(f"Zip file not found: {zip}")

        with tempfile.TemporaryDirectory() as extract_dir_str:
            extract_dir = Path(extract_dir_str)
            with zipfile.ZipFile(zip, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            for symbol_file in extract_dir.rglob("*"):
                if symbol_file.is_file() and (
                    symbol_file.suffix in (".sym", ".exe", ".dll")
                ):
                    try:
                        self.upload_artifact(symbol_file, server=server)
                    except build_environment.CommandFailedException as cfe:
                        if not ignore_failures:
                            raise
                        else:
                            logging.warning(
                                "Failed to process %s, due to %s", symbol_file, cfe
                            )
