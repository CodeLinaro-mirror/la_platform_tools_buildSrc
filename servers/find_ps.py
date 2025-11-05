"""
A utility script to find and kill processes that have open file handles or have their
current working directory set to a specified directory or any of its subdirectories.

This script is designed to solve issues where builds or other processes fail because
files or directories are locked by other processes.

The script operates in two modes:
1. Setup and Run Mode (default):
   - Creates a temporary virtual environment.
   - Installs the 'psutil' dependency from a local repository.
   - Re-launches itself within the virtual environment to execute the process killing logic.
   This mode ensures that the script has the necessary dependencies without polluting
   the global Python environment.

2. Run Mode (--run flag):
   - This mode is intended to be called from the setup and run mode.
   - It directly executes the logic to find and kill processes.
"""
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import List


def setup_and_run(target_dirs: List[str]) -> None:
    """
    Sets up a virtual environment, installs dependencies, and runs the main logic.

    This function creates a temporary virtual environment, installs the 'psutil'
    library from a local repository, and then re-launches this script within that
    environment to perform the actual process scanning and killing.

    This two-step process ensures that the script runs with the necessary
    dependencies without requiring them to be installed globally.

    Args:
        target_dirs: A list of directory paths to scan for process handles.
    """
    # Create a virtual environment in a temporary directory
    with tempfile.TemporaryDirectory() as venv_dir_str:
        venv_dir = Path(venv_dir_str)
        print(f"Creating virtual environment in {venv_dir}...")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])

        # Determine executable paths based on platform
        if sys.platform == "win32":
            pip_executable = venv_dir / "Scripts" / "pip.exe"
            python_executable = venv_dir / "Scripts" / "python.exe"
        else:
            pip_executable = venv_dir / "bin" / "pip"
            python_executable = venv_dir / "bin" / "python"

        # Install psutil from the local repository. This is necessary because this
        # script might be run in an environment where psutil is not installed.
        repo_path = (
            Path(__file__).parent.parent.parent.parent
            / "external"
            / "adt-infra"
            / "devpi"
            / "repo"
            / "simple"
        ).resolve()
        print(f"Installing psutil from {repo_path}...")
        subprocess.check_call(
            [str(pip_executable), "install", f"--find-links={repo_path}", "psutil"]
        )

        # Run the main logic in the virtual environment
        print(f"Re-running script to kill processes related to: {target_dirs}")
        cmd = [str(python_executable), __file__]
        for target_dir in target_dirs:
            cmd.extend(["--target-dir", target_dir])
        cmd.append("--run")
        subprocess.check_call(cmd)


def find_and_kill_processes(target_dir_strs: List[str]) -> None:
    """
    Finds and kills all processes that have a file handle open in the given
    directories or any of their subdirectories.

    It iterates through all running processes and checks two conditions:
    1. If the process's current working directory is within any of the target directories.
    2. If the process has any file open that is within any of the target directories.

    If either condition is met, the process is terminated.

    Args:
        target_dir_strs: A list of directory paths to scan.
    """
    import psutil

    target_dirs = [Path(d).resolve() for d in target_dir_strs]

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            # Check if the process's current working directory is in a target directory.
            if proc.is_running():
                cwd = Path(proc.cwd())
                if cwd.is_absolute():
                    for target_dir in target_dirs:
                        if cwd == target_dir or target_dir in cwd.parents:
                            print(
                                f"Killing process {proc.name()} ({proc.pid}) with CWD: {cwd} in {target_dir}"
                            )
                            proc.kill()
                            # Once killed, move to the next process.
                            # Further checks on this process will fail and fall through
                            # to the NoSuchProcess exception handler.
                            break

            # Check if the process has any open files in a target directory.
            # This will raise NoSuchProcess if the process was killed in the CWD check.
            for file in proc.open_files():
                try:
                    file_path = Path(file.path)
                    if file_path.is_absolute():
                        for target_dir in target_dirs:
                            if (
                                file_path == target_dir
                                or target_dir in file_path.parents
                            ):
                                print(
                                    f"Killing process {proc.name()} ({proc.pid}) holding handle on {file.path}"
                                )
                                proc.kill()
                                # Raise an exception to break out of all loops for this process
                                # and move to the next one.
                                raise psutil.NoSuchProcess(proc.pid, proc.name())
                except (OSError, ValueError):
                    # Some special file paths (e.g., '[eventpoll]') can cause errors, so we ignore them.
                    continue

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # NoSuchProcess is raised if the process is killed or terminates
            # during inspection. AccessDenied is raised if we don't have
            # permission to inspect the process. In either case, we move on.
            continue


def main():
    """
    Parses command-line arguments and orchestrates the script's execution flow.

    If the '--run' flag is present, it proceeds to find and kill processes.
    Otherwise, it initiates the setup process which creates a virtual
    environment and re-launches the script.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Find and kill processes holding locks in specified directories."
    )
    parser.add_argument(
        "--target-dir",
        required=True,
        nargs="+",
        help="One or more directories to scan for open handles.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Internal flag to indicate that the script is running in the virtual environment.",
    )
    args = parser.parse_args()

    if args.run:
        # This branch is executed inside the virtual environment
        find_and_kill_processes(args.target_dir)
    else:
        # This is the entry point when run directly
        print("Starting setup and run process...")
        setup_and_run(args.target_dir)


if __name__ == "__main__":
    main()