#!/usr/bin/env python3
#
# Copyright 2023 - The Android Open Source Project
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

import argparse
import logging
import platform
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from engine import bazel
from gemini.explainer import GeminiExplainer
from host import build_environment
from pipeline import Pipeline
from pipeline.context import BuildContext, copy_all, safe_copy_file
from pipeline.stages import (
    AospStage,
    EtsIntegrationStage,
    ReleasePackageStage,
    SymbolUploadStage,
    UnitTestStage,
    reclaim_and_stabilize_resources,
)
from services.log_handler import config_logging

_reclaim_and_stabilize_resources = reclaim_and_stabilize_resources

_UNIMPLEMENTED_TARGETS = {
    "windows-aarch64",
    "windows_aarch64",
}

_DEFAULT_CONFIG_BY_OS = {
    "linux": ["release", "debug_symbols", "ci", "dynamic"],
    "darwin": ["release", "debug_symbols", "ci"],
    # Windows uses a different feature for PDBs and is always on.
    "windows": ["release", "ci"],
}


def copy_bazel_logs(bzl: bazel.BazelCmd, logs_dir: Path):
    """Copies Bazel server logs, worker logs, and testlogs into logs_dir."""
    try:
        logs_dir_bazel = logs_dir / "bazel"
        logs_dir_bazel.mkdir(parents=True, exist_ok=True)
        output_base = Path(bzl.info["output_base"])

        logs = [
            Path(bzl.info["server_log"]),
            output_base / "server" / "jvm.out",
        ]
        if (output_base / "bazel-workers").is_dir():
            logs.extend((output_base / "bazel-workers").glob("*.log"))

        copy_all(logs, logs_dir_bazel, not_found_ok=True)

        if "bazel-testlogs" in bzl.info:
            testlogs_dir = Path(bzl.info["bazel-testlogs"])
            if testlogs_dir.is_dir():
                testlogs_dest = logs_dir / "testlogs"
                testlogs_dest.mkdir(parents=True, exist_ok=True)
                copied_testlogs = 0
                for ext in ("*.log", "*.xml"):
                    for f in testlogs_dir.rglob(ext):
                        rel = f.relative_to(testlogs_dir)
                        dest = testlogs_dest / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            safe_copy_file(f, dest)
                            copied_testlogs += 1
                        except FileNotFoundError:
                            continue
                        except (OSError, shutil.Error) as file_err:
                            logger.warning(
                                "Failed to copy testlog %s to %s: %s",
                                f,
                                dest,
                                file_err,
                            )
                if copied_testlogs > 0:
                    logger.info(
                        "Copied %d test log/XML file(s) to '%s'",
                        copied_testlogs,
                        testlogs_dest,
                    )
    except (OSError, shutil.Error, KeyError) as e:
        logger.warning("Failed to copy bazel logs: %s", e)


def get_common_build_flags(
    env: build_environment.BuildEnvironment,
    base_build_options: list[str],
) -> list[str]:
    """Assembles standardized Bazel build flags across pipelines."""
    flags = list(base_build_options) + [
        f"--build_metadata=ab_build_id={env.build_id}",
        f"--build_metadata=ab_target={env.build_target}",
        "--verbose_failures",
        "--stamp",
        f"--embed_label={env.build_id}",
        f"--@goldfish//emulator:build_id={env.build_id}",
        f"--@qemu//google/toolchain:build_id={env.build_id}",
        f"--@goldfish//emulator:is_presubmit={str(env.is_presubmit).lower()}",
    ]
    if (
        env.target_platform.startswith("linux")
        and env.is_release
        and not env.is_presubmit
    ):
        flags.append("--config=lto")
    return flags


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


def build_aosp(
    args,
    env: build_environment.BuildEnvironment,
    startup_options: list[str],
    build_options: list[str],
):
    """Executes the minimal AOSP build pipeline."""
    ctx = BuildContext(args, env, startup_options, build_options)
    pipeline = Pipeline(ctx, [AospStage()])
    pipeline.run()


def build_aemu(
    args,
    env: build_environment.BuildEnvironment,
    startup_options: list[str],
    build_options: list[str],
):
    """Executes the complete AEMU presubmit or postsubmit pipeline."""
    ctx = BuildContext(args, env, startup_options, build_options)
    pipeline = Pipeline(
        ctx,
        [
            ReleasePackageStage(),
            SymbolUploadStage(),
            UnitTestStage(),
            EtsIntegrationStage(),
        ],
    )
    pipeline.run()


def upload_symbols(env: build_environment.BuildEnvironment, bzl: bazel.BazelCmd):
    """Packages and uploads Crashpad symbols."""
    ctx = BuildContext(argparse.Namespace(dist=str(env.dist_dir)), env)
    ctx.active_bzl = bzl
    SymbolUploadStage().execute(ctx)


def _should_run_meson_generator(args, env):
    ctx = BuildContext(args, env)
    return ctx.should_run_meson_generator()


class _ConfigExtendAction(argparse.Action):
    """Replaces the default config list on first use and extends on repeated --config flags."""

    def __call__(self, parser, namespace, values, option_string=None):
        if not getattr(namespace, "_config_overridden", False):
            setattr(namespace, self.dest, [])
            namespace._config_overridden = True
        items = getattr(namespace, self.dest)
        if isinstance(values, list):
            items.extend(values)
        elif values is not None:
            items.append(values)


def _create_argument_parser() -> argparse.ArgumentParser:
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
        choices=["trusty_x64"]
        + list(bazel._PLATFORM_TARGETS_BY_NAME.keys())
        + sorted(_UNIMPLEMENTED_TARGETS),
        default=build_environment.default_target(),
        help=f"""The build target
        Must be one of:
        'trusty_x64' - Stock qemu, used by security team
        Or one of the following emulator releases:
        {", ".join([f"'{key}'" for key in bazel._PLATFORM_TARGETS_BY_NAME])},
        or pending targets:
        {", ".join([f"'{key}'" for key in sorted(_UNIMPLEMENTED_TARGETS)])}
        """,
    )
    parser.add_argument(
        "--aosp",
        action="store_true",
        help="""Build the AOSP configuration.
        Runs 'bazel build @goldfish//emulator:release' only, skipping internal tests,
        symbol uploads, and linters, and outputs the release distribution zip.""",
    )
    parser.add_argument(
        "--change_info",
        help="Path to the change_info.json file that is provided by the build bots",
    )
    parser.add_argument(
        "--config",
        type=str,
        nargs="*",
        action=_ConfigExtendAction,
        default=list(_DEFAULT_CONFIG_BY_OS[platform.system().lower()]),
        help="""The configurations to use.

        'ci' is intended for use by buildbots.

        'rcache' / 'connected' is intended for use by developers and will attempt to use remote caching.
        Note: Using rcache may make the build faster or a bit slower, depending on the hit rate.
        To use them, you'll need to configure your credentials:

        * glinux workstation - run `gcert`
        * Other machines: run `gcloud auth application-default login --project="emulator-builds"`
        """,
    )
    return parser


def main():
    config_logging()

    parser = _create_argument_parser()
    args = parser.parse_args()
    with (
        GeminiExplainer(args) as explainer,
        build_environment.create_build_environment(args) as env,
    ):
        explainer.env = env
        startup_options = []
        if env.tmp_dir:
            startup_options += [
                f"--output_base={env.tmp_dir / 'output'}",
                f"--install_base={env.tmp_dir / 'install'}",
            ]

        build_options = [f"--config={c}" for c in args.config]
        if not env.is_presubmit:
            build_options.append("--bes_keywords=ab-postsubmit")

        if args.target in _UNIMPLEMENTED_TARGETS:
            logger.info("Target %s is not yet implemented", args.target)
            return 0

        if args.target == "linux-aarch64":
            args.target = "linux_aarch64"
            env.target_platform = "linux_aarch64"

        if "trusty" in args.target:
            return build_trusty(args, env)

        if args.aosp:
            return build_aosp(args, env, startup_options, build_options)

        build_aemu(args, env, startup_options, build_options)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as e:  # noqa: BLE001
        # Top-level CLI entry point exception containment
        if not getattr(e, "_logged_by_pipeline", False):
            logger.fatal("Build failed due to %s", e)
        sys.exit(1)
