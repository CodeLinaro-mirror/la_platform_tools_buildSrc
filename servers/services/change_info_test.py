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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from services.change_info import ChangeInfo


class ChangeInfoTest(unittest.TestCase):

    def test_init_with_missing_or_none_file(self):
        info_none = ChangeInfo(None)
        self.assertEqual(info_none.data, {})

        info_missing = ChangeInfo("/non/existent/path/change_info.json")
        self.assertEqual(info_missing.data, {})

    def test_init_with_valid_file(self):
        data = {
            "changes": [
                {
                    "project": "platform/tools/buildSrc",
                    "projectPath": "tools/buildSrc",
                    "revisions": [{"gitRevision": "commit123"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)
            self.assertEqual(info.data, data)

    def test_get_commits_by_project(self):
        data = {
            "changes": [
                {
                    "project": "platform/tools/buildSrc",
                    "revisions": [
                        {"gitRevision": "rev1"},
                        {"gitRevision": "rev2"},
                    ],
                },
                {
                    "project": "platform/external/qemu",
                    "revisions": [
                        {"gitRevision": "qemu_rev1"},
                    ],
                },
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)
            self.assertEqual(
                info.get_commits_by_project("platform/tools/buildSrc"),
                ["rev1", "rev2"],
            )
            self.assertEqual(
                info.get_commits_by_project("platform/external/qemu"),
                ["qemu_rev1"],
            )
            self.assertEqual(
                info.get_commits_by_project("non_existent_project"),
                [],
            )

    def test_get_parent_diffs_by_project_success(self):
        data = {
            "changes": [
                {
                    "project": "platform/hardware/generic/goldfish",
                    "projectPath": "hardware/generic/goldfish",
                    "revisions": [{"gitRevision": "abc1234"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout="diff --git a/foo.cc b/foo.cc\n"
        )

        diffs = info.get_parent_diffs_by_project("hardware/generic/goldfish", mock_env)
        self.assertIn("hardware/generic/goldfish", diffs)
        self.assertEqual(
            diffs["hardware/generic/goldfish"], ["diff --git a/foo.cc b/foo.cc\n"]
        )
        mock_env.run.assert_called_once_with(
            cmd=["git", "-C", "hardware/generic/goldfish", "show", "abc1234"],
            capture_output=True,
            throw_on_failure=False,
        )

    def test_get_parent_diffs_by_project_failure(self):
        data = {
            "changes": [
                {
                    "project": "platform/hardware/generic/goldfish",
                    "projectPath": "hardware/generic/goldfish",
                    "revisions": [{"gitRevision": "badcommit"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=128, stdout="", stderr="fatal: bad object"
        )

        diffs = info.get_parent_diffs_by_project("hardware/generic/goldfish", mock_env)
        self.assertIn("hardware/generic/goldfish", diffs)
        self.assertTrue(
            any("Error getting diff" in d for d in diffs["hardware/generic/goldfish"])
        )

    def test_get_all_parent_diffs(self):
        data = {
            "changes": [
                {
                    "project": "project_a",
                    "projectPath": "path/to/a",
                    "revisions": [{"gitRevision": "rev_a"}],
                },
                {
                    "project": "project_b",
                    "projectPath": "path/to/b",
                    "revisions": [{"gitRevision": "rev_b"}],
                },
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout="diff output"
        )

        all_diffs = info.get_all_parent_diffs(mock_env)
        self.assertIn("path/to/a", all_diffs)
        self.assertIn("path/to/b", all_diffs)
        self.assertEqual(mock_env.run.call_count, 2)

    def test_get_clang_tidy_line_filter_json_cxx_files(self):
        data = {
            "changes": [
                {
                    "project": "hardware/generic/goldfish",
                    "projectPath": "hardware/generic/goldfish",
                    "revisions": [{"gitRevision": "cxx_rev"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        diff_sample = """diff --git a/emulator/base/foo.cc b/emulator/base/foo.cc
index 0000000..1111111 100644
--- a/emulator/base/foo.cc
+++ b/emulator/base/foo.cc
@@ -10,3 +15,5 @@ void foo() {
 // some line
@@ -30 +40,2 @@ int bar() {
diff --git a/emulator/base/bar.py b/emulator/base/bar.py
--- a/emulator/base/bar.py
+++ b/emulator/base/bar.py
@@ -1,5 +1,10 @@
"""
        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout=diff_sample
        )

        filter_json = info.get_clang_tidy_line_filter_json_by_project(
            "hardware/generic/goldfish", mock_env
        )
        self.assertTrue(bool(filter_json))
        parsed = json.loads(filter_json)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "emulator/base/foo.cc")
        # Line ranges: @@ -10,3 +15,5 @@ -> [15, 15+5-1] = [15, 19]
        #              @@ -30 +40,2 @@   -> [40, 40+2-1] = [40, 41]
        self.assertEqual(parsed[0]["lines"], [[15, 19], [40, 41]])

    def test_get_clang_tidy_line_filter_json_single_line_and_deletion(self):
        data = {
            "changes": [
                {
                    "project": "hardware/generic/goldfish",
                    "projectPath": "hardware/generic/goldfish",
                    "revisions": [{"gitRevision": "cxx_rev"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        diff_sample = """diff --git a/emulator/base/single.h b/emulator/base/single.h
--- a/emulator/base/single.h
+++ b/emulator/base/single.h
@@ -5 +10 @@ // single line replacement without length parameter
@@ -20,5 +30,0 @@ // deletion only
"""
        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout=diff_sample
        )

        filter_json = info.get_clang_tidy_line_filter_json_by_project(
            "hardware/generic/goldfish", mock_env
        )
        parsed = json.loads(filter_json)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["name"], "emulator/base/single.h")
        # Single line replacement at line 10 (length defaults to 1): [10, 10]
        # Deletion only (length 0) must be ignored
        self.assertEqual(parsed[0]["lines"], [[10, 10]])

    def test_get_clang_tidy_line_filter_json_empty_when_no_cpp(self):
        data = {
            "changes": [
                {
                    "project": "hardware/generic/goldfish",
                    "projectPath": "hardware/generic/goldfish",
                    "revisions": [{"gitRevision": "py_rev"}],
                }
            ]
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            info = ChangeInfo(f.name)

        diff_sample = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,5 +1,10 @@
"""
        mock_env = MagicMock()
        mock_env.run.return_value = subprocess.CompletedProcess(
            args=["git", "show"], returncode=0, stdout=diff_sample
        )

        filter_json = info.get_clang_tidy_line_filter_json_by_project(
            "hardware/generic/goldfish", mock_env
        )
        self.assertEqual(filter_json, "")


if __name__ == "__main__":
    unittest.main()
