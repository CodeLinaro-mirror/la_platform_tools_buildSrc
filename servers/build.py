#!/usr/bin/env python3
#
# Copyright 2023 - The Android Open Source Project
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

import argparse
import datetime
import logging
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

import bazel
import build_environment
import sym_upload
from change_info import ChangeInfo
from log_handler import config_logging


def copy_all(srcs: Iterable[Path], dest: Path):
    for f in srcs:
        f_dest = shutil.copy2(f, dest)
        logging.info("Copied '%s' to '%s'", f, f_dest)


def generate_aemu_bazel(args):
    with build_environment.create_build_environment(args) as env:
        amc = (
            env.repo_root
            / "external"
            / "qemu"
            / "google"
            / "toolchain"
            / "src"
            / "amc.py"
        )
        command = [
            env.python,
            amc,
            "-v",
            "bazel",
            "--aosp",
            env.repo_root,
            env.dist_dir,
            "--buildid",
            args.build_id,
        ]
        res = "no results."
        try:
            # Note, builds on windows can take quite some time.
            res = env.run(command, capture_output=env.is_windows(), timeout=7200)
        finally:
            logging.info("Completed aemu bazel generation: %s", res)


def build_trusty(args):
    zip_name = f"sdk-repo-{args.target}-qemu-{args.build_id}.zip"

    bld_dir = Path("out")
    bld_dir.mkdir(exist_ok=True, parents=True)

    with build_environment.create_build_environment(args) as env:
        toolchain = env.repo_root / "external" / "qemu" / "google" / "toolchain"
        build = toolchain / "build-qemu-trusty"
        command = [
            build,
            bld_dir,
            env.dist_dir / zip_name,
        ]
        env.run(command, timeout=1200)


def build_aemu(args):
    release_targets = [
        "//hardware/generic/goldfish/emulator:release",
        "//hardware/generic/goldfish/emulator:package_goldfish_symbols",
        "//hardware/generic/goldfish/emulator:package_goldfish_native_symbols",
    ]
    always_test_targets = ["//hardware/generic/goldfish/emulator:release_build_test"]
    test_targets = ["//hardware/generic/goldfish/emulator:emulator_unit_tests"]

    with build_environment.create_build_environment(args) as env:
        logs_dir = env.dist_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        build_id = "snapshot" if env.is_presubmit else env.build_id
        bzl = bazel.BazelCmd(env)

        bzl_debug = bzl.with_build_flags(
            [
                f"--config={args.config}",
                "--config=debug",
                f"--//hardware/generic/goldfish/emulator:build_id={build_id}",
            ]
        )
        targets = release_targets + always_test_targets
        # Skip tests on windows, we still need to figure out some build issues.
        if not env.is_windows():
            targets += test_targets
        bzl_debug.test(
            targets,
            invocation_flags=[
                "--test_output=errors",
                "--test_summary=detailed",
                "--verbose_failures",
                "--verbose_explanations",
                f"--explain={logs_dir / 'bazel_test_debug_explain.log'}",
                "--build_metadata=test_definition_name=android_emulator/test_debug",
                f"--build_metadata=ab_build_id={env.build_id}",
                f"--build_metadata=ab_target={env.build_target}",
            ],
            allow_analysis_cache_discard=True,
        )
        artifacts = bzl_debug.query_artifacts(release_targets)
        copy_all(artifacts, env.dist_dir)

        bzl_release = bzl.with_build_flags(
            [
                f"--config={args.config}",
                "--config=release",
                f"--//hardware/generic/goldfish/emulator:build_id={build_id}",
            ]
        )
        bzl_release.test(
            release_targets + always_test_targets,
            invocation_flags=[
                "--verbose_failures",
                "--verbose_explanations",
                f"--explain={logs_dir / 'bazel_release_explain.log'}",
                "--build_metadata=test_definition_name=android_emulator/release",
                f"--build_metadata=ab_build_id={env.build_id}",
                f"--build_metadata=ab_target={env.build_target}",
            ],
            allow_analysis_cache_discard=True,
        )
        artifacts = bzl_release.query_artifacts(release_targets)
        copy_all(artifacts, env.dist_dir)

        if env.crashpad_symbol_server_key:
            upload_symbols(env, bzl_release)
        else:
            logging.warning("No server API key available, not uploading symbols.")


def upload_symbols(env: build_environment.BuildEnvironment, bzl: bazel.BazelCmd):
    uploader = sym_upload.Symuploader(env, bzl)

    if env.is_windows():
        symbol_zip_file = bzl.query_artifacts(
            ["//hardware/generic/goldfish/emulator:release"]
        )[0]
        # Ignoring an issue around processing of .dll's for now
        uploader.upload_from_zip(symbol_zip_file, ignore_failures=True)
    else:
        symbol_zip_file = bzl.query_artifacts(
            ["//hardware/generic/goldfish/emulator:package_goldfish_symbols"]
        )[0]
        uploader.upload_from_zip(symbol_zip_file)


def main():
    config_logging()

    parser = argparse.ArgumentParser(
        description="""Builds the android emulator by invoking bazel.

        The build script will invoke as series of bazel commands to construct the emulator distribution.
        It is self contained will use bazel and clang from AOSP

        To run this on a development machine you will have to provide the `--config` parameter.
        """,
        epilog="""For example:

        On your darwin development machine:

        ./build.py --dist /tmp/dist --build-id P123 --target mac_aarch64 --config rcache

        """,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--dist",
        type=str,
        required=True,
        help="The destination directory, artifacts will be placed in this directory.",
    )
    parser.add_argument(
        "--build-id",
        type=str,
        default="dev",
        dest="build_id",
        help="The build number used. Presubmit builds should start with the letter P.",
    )
    parser.add_argument(
        "--target",
        type=str,
        choices=["trusty_x64"] + list(bazel._PLATFORM_TARGETS_BY_NAME.keys()),
        default=platform.system(),
        help=f"""The build target
        Must be one of:
        'trusty_x64' - Stock qemu, used by security team
        Or one of the following emulator releases:
        {", ".join([f"'{key}'" for key in bazel._PLATFORM_TARGETS_BY_NAME.keys()])}
        """,
    )
    parser.add_argument(
        "--change_info",
        help="Path to the change_info.json file that is provided by the build bots",
    )
    parser.add_argument(
        "--force_generate_aemu_bazel",
        action="store_true",
        help="Force the building of the qemu meson packages to generate the bazel build files.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="ci",
        help="""The configuration to use.

        'ci' is intended for use by buildbots.

        'rcache' is intended for use by developers and will attempt to use remote caching.
        Note: Using rcache may make the build faster or a bit slower, depending on the hit rate.
        To use 'rcache', you'll need to configure your credentials:

        **glinux workstation:**

        1. Add the following line to `~/.bazelrc`:
              common --credential_helper=/google/src/head/depot/google3/devtools/blaze/bazel/credhelper/credhelper

        2. Run `gcert`

        **Other machines:**

        1. Add the following line to `~/.bazelrc` (`%%USERPROFILE%%\\.bazelrc` on Windows):
               common --google_default_credentials

        2. Run:
               gcloud auth application-default login --project="emulator-builds"
               gcloud auth application-default set-quota-project emulator-builds
        """,
    )

    args = parser.parse_args()
    if "trusty" in args.target:
        return build_trusty(args)

    change_info = ChangeInfo(args.change_info)
    if args.force_generate_aemu_bazel or (
        args.build_id.startswith("P")
        and change_info.get_commits_by_project("platform/external/qemu")
    ):
        logging.info("Qemu changes detected, generating bazel build files.")
        generate_aemu_bazel(args)
    build_aemu(args)


if __name__ == "__main__":
    start_time = time.monotonic()
    try:
        main()
    except Exception as e:
        logging.fatal("Build failed due to %s", e)
        sys.exit(1)
    finally:
        end_time = time.monotonic()
        logging.info(
            "Completed in: %s", datetime.timedelta(seconds=end_time - start_time)
        )
