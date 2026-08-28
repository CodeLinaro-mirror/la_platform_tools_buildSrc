# Copyright 2024 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import re
import logging
from pathlib import Path


class ChangeInfo:
    """
    Parses Android change info JSON data. This information is present
    for presubmit runs and contains details about which changes are
    involved in this run.

    Attributes:
        data (list): A list of changes, each represented as a dictionary.

    Example usage:
    ```python
    change_info = ChangeInfo("change_info.json")
    commits = change_info.get_commits_by_project("platform/tools/buildSrc")
    print(commits)
    ```
    """

    def __init__(self, filename):
        """
        Initializes ChangeInfo with data from a JSON file.

        Args:
            filename (str): The path to the JSON file containing change info.
        """
        if filename and Path(filename).exists():
            with open(filename, "r") as f:
                self.data = json.load(f)
                logging.info("Change info file loaded successfully: %s.", self.data)
        else:
            self.data = {}
            logging.warning("No change info file provided.")

    def get_commits_by_project(self, project):
        """
        Retrieves commits related to a specific project that are included
        in this presubmit run.

        Args:
            project (str): The path of the project.

        Returns:
            list: A list of commits hashes associated with the given project path.
        """
        commits = []
        for change in self.data.get("changes", []):
            if change["project"] == project:
                for revision in change["revisions"]:
                    commits.append(revision["gitRevision"])
        return commits

    def get_all_parent_diffs(self, bazel_env):
        """
        Retrieves the `git show` output for the parent commits of all
        changes found in the loaded JSON data.

        Args:
            bazel_env: An instance of a BazelEnvironment class.

        Returns:
            dict: A dictionary where keys are project paths and values are
                  lists of git show outputs for each parent commit.
        """
        all_diffs = {}
        project_paths = set()
        for change in self.data.get("changes", []):
            project_path = change.get("projectPath")
            if project_path:
                project_paths.add(project_path)

        for project_path in project_paths:
            diffs = self.get_parent_diffs_by_project(project_path, bazel_env)
            all_diffs.update(diffs)

        return all_diffs

    def get_parent_diffs_by_project(self, project_path, bazel_env):
        """
        Retrieves the `git show` output for the parent commits of all changes
        within a specific project.

        Args:
            project_path (str): The path to the Git repository.
            bazel_env: An instance of a BazelEnvironment class.

        Returns:
            dict: A dictionary where the key is the project path and the value
                  is a list of git show outputs for each parent commit.
        """
        diffs = {project_path: []}
        for change in self.data.get("changes", []):
            if change.get("projectPath") == project_path:
                for revision in change.get("revisions", []):
                    commit_data = revision.get("gitRevision", "HEAD")
                    if commit_data:
                        logging.info(
                            "Running `git show` for commit %s in project %s.",
                            commit_data,
                            project_path,
                        )

                        result = bazel_env.run(
                            cmd=["git", "-C", project_path, "show", commit_data],
                            capture_output=True,
                            throw_on_failure=False,  # Don't raise an exception on command failure
                        )

                        if result.returncode == 0:
                            diffs[project_path].append(result.stdout)
                        else:
                            logging.error(
                                "Failed to run `git show` for commit %s. Error: %s",
                                commit_data,
                                result.stderr,
                            )
                            diffs[project_path].append(
                                f"Error getting diff for commit {commit_data}: {result.stderr}"
                            )
        return diffs

    def get_clang_tidy_line_filter_json(self, bazel_env):
        """
        Computes a JSON string suitable for clang-tidy's --line-filter, based on
        the modified files in the current changes. Extracts unified diff hunks to map
        line numbers for modified C/C++ files.
        """
        all_diffs = self.get_all_parent_diffs(bazel_env)

        # Matches git diff file header line.
        # Example: "diff --git a/emulator/base/foo.cc b/emulator/base/foo.cc"
        #   - Group 1 (old path): "emulator/base/foo.cc"
        #   - Group 2 (new path): "emulator/base/foo.cc"
        file_pattern = re.compile(r"^diff --git a/(.*?) b/(.*?)$")

        # Matches unified diff hunk headers specifying modified line ranges.
        # Format: @@ -<old_start>[,<old_count>] +<new_start>[,<new_count>] @@
        # Examples:
        #   - "@@ -10,5 +20,15 @@ void foo() {" -> Group 1: 20 (start line), Group 2: 15 (line count)
        #   - "@@ -5 +5 @@"                       -> Group 1: 5 (start line),  Group 2: None (count defaults to 1)
        #   - "@@ -10,5 +20,0 @@"                 -> Group 1: 20 (start line), Group 2: 0 (deletions only)
        chunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

        filters = []

        for project_path, diffs_list in all_diffs.items():
            # Extract changed files and their modified line ranges [start, end] from each commit's unified diff.
            for diff_text in diffs_list:
                file_lines = {}
                current_file = None

                for line in diff_text.splitlines():
                    file_match = file_pattern.match(line)
                    if file_match:
                        current_file = file_match.group(2)
                        if current_file not in file_lines:
                            file_lines[current_file] = []
                        continue

                    chunk_match = chunk_pattern.match(line)
                    if chunk_match and current_file:
                        start_line = int(chunk_match.group(1))
                        length = chunk_match.group(2)
                        length = int(length) if length is not None else 1
                        if length > 0:
                            end_line = start_line + length - 1
                            # Combine contiguous ranges if needed, but clang-tidy accepts disjoint or overlapping
                            file_lines[current_file].append([start_line, end_line])

                # Only keep C and C++ source and header files for clang-tidy analysis.
                for file_name, lines in file_lines.items():
                    if lines and file_name.endswith(
                        (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")
                    ):
                        filters.append({"name": file_name, "lines": lines})

        return json.dumps(filters) if filters else ""
