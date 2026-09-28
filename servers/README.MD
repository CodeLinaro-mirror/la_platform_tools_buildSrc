# Android Emulator CI & Build Automation (`tools/buildSrc`)

This directory contains the build orchestration scripts executed by the Android
build bots (`go/ab`) for continuous integration, presubmit validation, release
packaging, and symbol generation across Linux, macOS, and Windows.

## Overview

The primary orchestrator is [`build.py`](build.py). It invokes the AOSP hermetic
prebuilt Bazel binary (`prebuilts/bazel/<os>-x86_64/bazel`) using rules from
`hardware/generic/goldfish`, `hardware/google/aemu`, and `third_party/qemu`.

### Invocation Modes

- **Presubmit Runs**: Triggered when a Gerrit change is uploaded. Identified by
  a build ID starting with `P` (e.g., `P12345678`). Executes a 3-stage fast-fail
  pipeline.
- **Postsubmit Runs**: Triggered when changes are merged into the repository.
  Executes a full test matrix, builds release archives, and uploads crashpad
  symbol files (`.sym`, `.pdb`) to staging and production symbol collectors.
- **Local Developer Runs**: Developers can reproduce CI builds using remote
  caching or local execution.

---

## Package Architecture

The codebase is organized into four modular, cohesive subpackages under
`servers/`:

- **[`pipeline/`](pipeline/)**: Build orchestration lifecycle and execution
  stages.
  - [`context.py`](pipeline/context.py): Execution context, build options,
    artifact handling, and log collection.
  - [`stage.py`](pipeline/stage.py): Abstract `Stage` contract (`pre_execute`,
    `execute`, `on_failure`).
  - [`stages.py`](pipeline/stages.py): Concrete build and test stages
    (`ClangTidyStage`, `ReleasePackageStage`, `UnitTestStage`,
    `EtsIntegrationStage`, `PostsubmitStage`, `SymbolUploadStage`).
  - [`pipeline.py`](pipeline/pipeline.py): Pipeline execution loop with
    lifecycle telemetry and error handling.
  - [`pipeline_test.py`](pipeline/pipeline_test.py): Co-located unit tests.
- **[`engine/`](engine/)**: Bazel wrapper and invocation builders.
  - [`bazel.py`](engine/bazel.py): Low-level `BazelCmd` runner, info parsing,
    build/test execution, artifact querying.
  - [`invocation.py`](engine/invocation.py): `BazelInvocation` dataclass and
    fluent `BazelInvocationBuilder`.
  - [`invocation_test.py`](engine/invocation_test.py): Co-located unit tests.
- **[`host/`](host/)**: Host environment abstraction and system resource
  diagnostics.
  - [`build_environment.py`](host/build_environment.py): Platform-specific
    environments (`LinuxBuildEnvironment`, `MacOSBuildEnvironment`,
    `WindowsBuildEnvironment`).
  - [`host_resources.py`](host/host_resources.py): Cross-platform memory
    telemetry (`/proc/meminfo`, `sysctl`, `GlobalMemoryStatusEx`) and dynamic
    constraint checks.
  - [`build_environment_test.py`](host/build_environment_test.py),
    [`host_resources_test.py`](host/host_resources_test.py): Co-located unit
    tests.
- **[`services/`](services/)**: Integrations, telemetry, and diagnostics.
  - [`change_info.py`](services/change_info.py): Gerrit `change_info.json`
    parser.
  - [`sym_upload.py`](services/sym_upload.py): Crashpad/Breakpad symbol uploader
    with staging and production support.
  - [`log_handler.py`](services/log_handler.py): Console logging and secret
    masking.
  - [`change_info_test.py`](services/change_info_test.py),
    [`sym_upload_test.py`](services/sym_upload_test.py): Co-located unit tests.
- **[`gemini/`](gemini/)**: Gemini AI build failure explainer and credentials.
  - [`auth.py`](gemini/auth.py): Authenticator resolving API keys via
    environment or GCP Secret Manager.
  - [`client.py`](gemini/client.py): REST client for Google Generative Language
    API.
  - [`explainer.py`](gemini/explainer.py): Context manager capturing build
    logs/diffs and prompting Gemini Flash.
  - [`auth_test.py`](gemini/auth_test.py),
    [`client_test.py`](gemini/client_test.py),
    [`explainer_test.py`](gemini/explainer_test.py): Co-located unit tests.
- **CLI & Hook Entrypoints**:
  - [`build.py`](build.py): Main CI and development build script.
  - [`build_test.py`](build_test.py): CLI dispatch and integration test suite.
  - [`buildifier.py`](buildifier.py): Buildifier pre-upload formatting hook.
  - [`bazel_lockfile.py`](bazel_lockfile.py): Bazel lockfile update and
    validation hook.
  - [`__init__.py`](__init__.py): Root package exports and dynamic legacy import
    redirection.

---

## Command Line Usage

### Standard CI Invocation

```bash
python3 tools/buildSrc/servers/build.py \
    --dist /tmp/dist \
    --build-id P123456 \
    --target linux_x64 \
    --config release debug_symbols ci dynamic
```

### Common Targets

- `linux_x64`: Standard Linux 64-bit emulator binaries and tests.
- `mac_aarch64`: Apple Silicon (M1/M2/M3/M4) macOS host release.
- `mac_x64`: Intel 64-bit macOS host release.
- `windows_x64`: Windows 64-bit emulator release (MSVC / Clang).
- `trusty_x64`: Stock QEMU target used by the security team.

### Useful Flags

- `--aosp`: Minimal build executing exclusively `@goldfish//emulator:release`
  under `--config=aosp`, skipping internal tests, symbol generation, and
  linters.
- `--change_info <path>`: Path to Gerrit `change_info.json`. Enables scoped
  clang-tidy linting and automatic QEMU Meson generator checks.
- `--config rcache`: Configures remote caching for local developer builds
  (requires `gcert` on gLinux or `gcloud auth` application-default login).

---

## Pipeline Execution Stages

### 1. Clang-Tidy Pre-Pass (Presubmit)

When `change_info.json` specifies modified C/C++ files under
`hardware/generic/goldfish`:

- Extracts diff line hunks into a fine-grained
  `--@goldfish_build//:clang_tidy_line_filter` JSON.
- Scopes Bazel analysis to the packages containing modified files
  (`_find_tidy_targets()`).
- Runs a fast-fail check (`--test_tag_filters=tidy-test`) before heavy
  compilation begins.

### 2. Release Packaging & Boot Tests (Presubmit)

- Builds `@goldfish//emulator:release` and related delivery archives under
  `--config=release`.
- Preserves ThinLTO optimizations for shipping binaries.
- Executes fast boot tests (`@goldfish//emulator/launcher:boot_tests`).
- Immediately stages release artifacts into `--dist` so partial outputs are
  preserved if subsequent tests fail.

### 3. Fast-Fail Unit Tests (Presubmit)

- Runs all unit tests (`@goldfish//...`) with ThinLTO explicitly disabled
  (`--features=-thin_lto`) and `--build_tests_only`.
- Bypasses ~185k redundant LTO backend compile actions and remote cache checks,
  failing fast on logic defects.

### 4. ETS Integration Tests (Presubmit)

- Preceded by a **Resource Stabilization Hook** (`bzl.shutdown()` + `os.sync()`)
  to reclaim resident JVM heap (2.5–4 GB) and flush buffers.
- Runs heavy emulator integration tests (`@goldfish_test//ets:...`) with bounded
  JVM heap (`--host_jvm_args=-Xmx1g`).
- On memory-constrained macOS builders, throttles concurrency (`--jobs=2`,
  `--local_resources=memory=HOST_RAM*.3`) to eliminate memory-pressure crashes
  and swap trashing.

### 5. Symbol Generation & Upload (Postsubmit)

- Packages Breakpad/Crashpad symbols (`package_goldfish_symbols`).
- Uses [`sym_upload.py`](sym_upload.py) to push symbol files to:
  - Production: `https://prod-crashsymbolcollector-pa.googleapis.com`
  - Staging: `https://staging-crashsymbolcollector-pa.googleapis.com`

---

## Failure Diagnostics & Telemetry

### Gemini AI Root-Cause Explainer

[`explainer.py`](gemini/explainer.py) wraps build execution. When an unhandled
exception or test failure occurs, it extracts the last 2,000 log lines alongside
git diffs and queries Gemini Flash to summarize the root cause directly into the
bot log.

### Bazel Log Extraction

On build completion or failure, [`copy_bazel_logs()`](build.py) extracts:

- Bazel server logs (`server_log`, `jvm.out`, worker logs) into
  `<dist>/logs/bazel/`.
- Test execution logs and XMLs (`test.log`, `test.xml`) into
  `<dist>/logs/testlogs/`.
- Bazel execution profiles (`command.profile.gz`, `unit_tests.profile.gz`,
  `ets.profile.gz`).

---

## Pre-Upload Hooks

The following standalone utilities are configured in
[`GLOBAL-PREUPLOAD.cfg`](../../hardware/generic/goldfish/development/GLOBAL-PREUPLOAD.cfg):

- [`buildifier.py`](buildifier.py): Validates and formats Starlark/Bazel files
  (`BUILD`, `WORKSPACE`, `.bzl`).
- [`bazel_lockfile.py`](bazel_lockfile.py): Verifies and updates
  `MODULE.bazel.lock` dependencies.
