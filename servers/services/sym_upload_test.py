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

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from host import build_environment
from services.sym_upload import Symuploader


class SymUploadTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock(spec=build_environment.BuildEnvironment)
        self.mock_env.crashpad_server = (
            "https://prod-crashsymbolcollector-pa.googleapis.com"
        )
        self.mock_env.crashpad_symbol_server_key = "test_key_123"
        self.mock_env.is_windows.return_value = False

        self.mock_bzl = MagicMock()
        self.uploader = Symuploader(self.mock_env, self.mock_bzl)

    def test_upload_artifact_linux(self):
        self.mock_env.is_windows.return_value = False
        sym_file = Path("/tmp/emulator.sym")

        self.uploader.upload_artifact(sym_file)

        self.mock_bzl.run.assert_called_once_with(
            target="@breakpad//:sym_upload",
            params=[
                "-p",
                "sym-upload-v2",
                "-k",
                "test_key_123",
                str(sym_file),
                "https://prod-crashsymbolcollector-pa.googleapis.com",
            ],
        )

    def test_upload_artifact_windows(self):
        self.mock_env.is_windows.return_value = True
        exe_file = Path("C:/tmp/emulator.exe")

        self.uploader.upload_artifact(exe_file, server="https://staging.googleapis.com")

        self.mock_bzl.run.assert_called_once_with(
            target="@breakpad//:sym_upload",
            params=[
                "-p",
                str(exe_file),
                "https://staging.googleapis.com",
                "test_key_123",
            ],
        )

    def test_upload_artifact_exit_code_2_ignored(self):
        # Exit code 2 means "symbols already present on server", should be silently accepted.
        failed_proc = subprocess.CompletedProcess(args=[], returncode=2)
        self.mock_bzl.run.side_effect = build_environment.CommandFailedException(
            "Already present", failed_proc
        )

        sym_file = Path("/tmp/emulator.sym")
        # Should not raise exception
        self.uploader.upload_artifact(sym_file)

    def test_upload_artifact_other_failure_raises(self):
        failed_proc = subprocess.CompletedProcess(args=[], returncode=1)
        self.mock_bzl.run.side_effect = build_environment.CommandFailedException(
            "Network error", failed_proc
        )

        sym_file = Path("/tmp/emulator.sym")
        with self.assertRaises(build_environment.CommandFailedException):
            self.uploader.upload_artifact(sym_file)

    def test_upload_artifacts_queries_targets(self):
        sym1 = Path("/out/sym1.sym")
        sym2 = Path("/out/sym2.sym")
        self.mock_bzl.query_artifacts.return_value = [sym1, sym2]

        self.uploader.upload_artifacts(["@goldfish//emulator:package_goldfish_symbols"])

        self.mock_bzl.query_artifacts.assert_called_once_with(
            ["@goldfish//emulator:package_goldfish_symbols"]
        )
        self.assertEqual(self.mock_bzl.run.call_count, 2)

    def test_upload_from_zip_extracts_and_uploads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "symbols.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("emulator.sym", "MODULE Linux x86_64...")
                zf.writestr("ignored.txt", "Some text file")
                zf.writestr("sub/libOpenglRender.so.sym", "MODULE Linux...")

            self.uploader.upload_from_zip(zip_path)

            # Exactly 2 symbol files should be uploaded, ignoring .txt
            self.assertEqual(self.mock_bzl.run.call_count, 2)

    def test_upload_from_zip_missing_raises_file_not_found(self):
        missing_zip = Path("/non/existent/symbols.zip")
        with self.assertRaises(FileNotFoundError):
            self.uploader.upload_from_zip(missing_zip)

    def test_upload_from_zip_ignore_failures(self):
        failed_proc = subprocess.CompletedProcess(args=[], returncode=1)
        self.mock_bzl.run.side_effect = build_environment.CommandFailedException(
            "Upload failed", failed_proc
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "symbols.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("broken.sym", "broken content")

            # Should not raise when ignore_failures is True
            with self.assertLogs(level="WARNING") as cm:
                self.uploader.upload_from_zip(zip_path, ignore_failures=True)
            self.assertTrue(any("Failed to process" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
