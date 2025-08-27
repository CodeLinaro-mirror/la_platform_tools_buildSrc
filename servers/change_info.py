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
                                parent_id,
                                result.stderr,
                            )
                            diffs[project_path].append(
                                f"Error getting diff for commit {parent_id}: {result.stderr}"
                            )
        return diffs
