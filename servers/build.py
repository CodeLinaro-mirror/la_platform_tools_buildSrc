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
from typing import Iterable, List

import bazel
import build_environment
import sym_upload
from change_info import ChangeInfo
from gemini_explainer import GeminiExplainer
from log_handler import config_logging

_DEFAULT_CONFIG_BY_OS = {
    "linux": ["release", "ci", "remote"],
    "darwin": ["release", "ci"],
    "windows": ["release", "ci"],
}


def copy_all(srcs: Iterable[Path], dest: Path, not_found_ok=False):
    for f in srcs:
        try:
            f_dest = shutil.copy2(f, dest)
            logging.info("Copied '%s' to '%s'", f, f_dest)
        except FileNotFoundError:
            if not_found_ok:
                continue
            raise


def copy_bazel_logs(bzl: bazel.BazelCmd, logs_dir: Path):
    logs_dir = logs_dir / "bazel"
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_base = Path(bzl.info["output_base"])

    logs = [
        Path(bzl.info["server_log"]),
        output_base / "server" / "jvm.out",
    ]
    if (output_base / "bazel-workers").is_dir():
        logs.extend((output_base / "bazel-workers").glob("*.log"))

    copy_all(logs, logs_dir, not_found_ok=True)


def generate_aemu_bazel(args, env, startup_options, build_options):
    bzl = bazel.BazelCmd(env, startup_options=startup_options).with_build_flags(
        build_options
    )

    res = "no results."
    try:
        # Note, builds on windows can take quite some time.
        logging.info("Starting aemu bazel generation")
        res = bzl.run(
            target="@qemu//google/toolchain:amc",
            params=[
                "-v",
                "--bazel_startup_options={}".format(",".join(startup_options)),
                "--bazel_build_options={}".format(",".join(build_options)),
                "bazel",
                "--aosp",
                env.repo_root,
                env.dist_dir,
                "--config",
                env.repo_root
                / "third_party"
                / "qemu"
                / "google"
                / "toolchain"
                / "qemu-build-config.jsonc",
                "--buildid",
                args.build_id,
            ],
            allow_analysis_cache_discard=True,
            timeout=7200,
        )
    finally:
        logging.info("Completed aemu bazel generation: %s", res)


def build_trusty(args, env):
    zip_name = f"sdk-repo-{args.target}-qemu-{args.build_id}.zip"

    bld_dir = Path("out")
    bld_dir.mkdir(exist_ok=True, parents=True)

    toolchain = env.repo_root / "third_party" / "qemu" / "google" / "toolchain"
    build = toolchain / "build-qemu-trusty"
    command = [
        build,
        bld_dir,
        env.dist_dir / zip_name,
    ]
    env.run(command, timeout=1200)


def build_aemu(
    env: build_environment.BuildEnvironment,
    startup_options: List[str],
    build_options: List[str],
):
    release_targets = [
        "@goldfish//emulator:release",
        "@goldfish//emulator:package_goldfish_symbols",
        "@goldfish//emulator:package_goldfish_native_symbols",
    ]
    if env.target_platform.startswith("linux"):
        release_targets.append("@goldfish//emulator:release_unstripped")
        release_targets.append("@goldfish//emulator:release_internal")
    # Needs special handling on windows.
    android_ets_zip = ["@goldfish_test//ets:android_ets_zip"]
    always_test_targets = [
        "@goldfish//emulator:release_build_test",
        "@goldfish_build//:sanity_checks",
    ]
    test_targets = [
        # Build everything (including tests marked manual) but only run the non-manual ones...
        "@goldfish//...",
        # clang_tidy_report needs visibility checking to be disabled and should be run separately.
        "-@goldfish//:clang_tidy_report",
        # Crashpad tests currently fail on Windows.
        "-@goldfish//emulator:external_unit_tests",
        # We also want to run the manual boot_tests.
        "@goldfish//emulator/launcher:boot_tests",
    ]
    external_tests = ["@goldfish//emulator:external_unit_tests"]
    cts_test_targets = ["@goldfish_test//cts:postsubmit"]

    ets_test_targets = [
        # Do not run all of @goldfish_test as there are test rules which will only pass when run as
        # a part of tradefed.
        # Goldfish E2E test libraries.
        "@goldfish_test//testlib/...",
    ]
    # Run only a subset of tests in presubmit.
    if env.is_presubmit:
        ets_test_targets.append("@goldfish_test//ets:presubmit")
    else:
        ets_test_targets.append("@goldfish_test//ets:postsubmit")

    logs_dir = env.dist_dir / "logs"
    (logs_dir / "bazel").mkdir(parents=True, exist_ok=True)
    build_id = "snapshot" if env.is_presubmit else env.build_id
    bzl_release = bazel.BazelCmd(env, startup_options=startup_options).with_build_flags(
        build_options
        + [
            f"--build_metadata=ab_build_id={env.build_id}",
            f"--build_metadata=ab_target={env.build_target}",
            "--verbose_failures",
            "--build_manual_tests",
            f"--@goldfish//emulator:build_id={build_id}",
        ]
    )

    if not env.is_windows():
        # ETS zip file does not yet build on Windows.
        release_targets += android_ets_zip

    targets = release_targets + test_targets + always_test_targets
    if not env.is_windows():
        # crashpad tests and ETS don't currently run on Windows.
        targets += external_tests + ets_test_targets

    if not env.is_presubmit and not env.is_windows() and not env.is_macos():
        # b/490122946 some buildbot macs can not execute cts-tradefed due to
        # the missing executable `realpath`, which cts-tradefed assumes
        # exists.
        targets += cts_test_targets

    invocation_flags = [
        "--config=ants",
        f"--profile={logs_dir / 'bazel' / 'command.profile.gz'}",
        "--build_metadata=test_definition_name=android_emulator/release",
        "--test_output=errors",
        "--test_summary=detailed",
    ]
    if not env.is_presubmit:
        invocation_flags.append("--nocache_test_results")
    try:
        bzl_release.test(
            targets,
            invocation_flags=invocation_flags,
            allow_analysis_cache_discard=True,
            timeout=(3600 * 5 if env.is_macos() else 3600),
        )
    except build_environment.CommandFailedException:
        copy_bazel_logs(bzl_release, logs_dir)
        raise
    artifacts = bzl_release.query_artifacts(release_targets)
    copy_all(artifacts, env.dist_dir)

    if env.crashpad_symbol_server_key:
        upload_symbols(
            env,
            bzl_release.with_build_flags(
                bzl_release.build_flags + ("--config=no_sponge",),
            ),
        )
    else:
        logging.warning("No server API key available, not uploading symbols.")


def upload_symbols(env: build_environment.BuildEnvironment, bzl: bazel.BazelCmd):
    uploader = sym_upload.Symuploader(env, bzl)

    if env.is_windows():
        symbol_zip_file = bzl.query_artifacts(["@goldfish//emulator:release"])[0]
        # Ignoring an issue around processing of .dll's for now
        uploader.upload_from_zip(symbol_zip_file, ignore_failures=True)
        if not env.is_presubmit:
            logging.info("Pushing symbols to staging as well.")
            uploader.upload_from_zip(
                symbol_zip_file, ignore_failures=True, server=env.CRASHPAD_STAGING
            )
    else:
        symbol_zip_file = bzl.query_artifacts(
            ["@goldfish//emulator:package_goldfish_symbols"]
        )[0]
        uploader.upload_from_zip(symbol_zip_file)
        if not env.is_presubmit:
            logging.info("Pushing symbols to staging as well.")
            uploader.upload_from_zip(symbol_zip_file, server=env.CRASHPAD_STAGING)


def _should_run_meson_generator(args):
    change_info = ChangeInfo(args.change_info)

    if args.force_generate_aemu_bazel:
        return True

    # Don't try to run AMC for TSAN or ASAN builds.
    if args.config != "cirelease":
        return False

    # Always run in post submit.
    if not args.build_id.startswith("P"):
        return True

    # Always run presubmit when qemu project is affected or projects that affect
    # AMC tool
    if change_info.get_commits_by_project("platform/external/qemu"):
        return True
    if change_info.get_commits_by_project("trusty/external/qemu-meson"):
        return True
    if change_info.get_commits_by_project("platform/hardware/google/aemu"):
        return True

    # We now always run the generator for Linux.
    # TODO(whollins): Consider adding a mechanism to force NOT running the generator.
    if platform.system().lower() == "linux":
        return True

    return False


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
        nargs="*",
        default=_DEFAULT_CONFIG_BY_OS[platform.system().lower()],
        help="""The configurations to use.

        'ci' is intended for use by buildbots.

        'rcache' / 'connected' is intended for use by developers and will attempt to use remote caching.
        Note: Using rcache may make the build faster or a bit slower, depending on the hit rate.
        To use them, you'll need to configure your credentials:

        * glinux workstation - run `gcert`
        * Other machines: run `gcloud auth application-default login --project="emulator-builds"`
        """,
    )

    args = parser.parse_args()
    with (
        GeminiExplainer(args) as _,
        build_environment.create_build_environment(args) as env,
    ):
        startup_options = []
        if env.tmp_dir:
            startup_options += [
                f"--output_base={env.tmp_dir / 'output'}",
                f"--install_base={env.tmp_dir / 'install'}",
            ]

        build_options = [f"--config={c}" for c in args.config]
        if not env.is_presubmit:
            build_options.append("--bes_keywords=ab-postsubmit")

        if "trusty" in args.target:
            return build_trusty(args, env)

        if _should_run_meson_generator(args):
            logging.info("Qemu changes detected, generating bazel build files.")
            generate_aemu_bazel(
                args, env, startup_options, build_options + ["--config=no_sponge"]
            )
        build_aemu(env, startup_options, build_options)


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
