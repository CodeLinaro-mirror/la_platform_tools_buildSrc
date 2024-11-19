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
        else:
            self.data = []
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
        for change in self.data["changes"]:
            if change["project"] == project:
                for revision in change["revisions"]:
                    commits.append(revision["gitRevision"])
        return commits