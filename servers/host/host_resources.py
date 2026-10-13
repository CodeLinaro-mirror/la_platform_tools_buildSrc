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

import logging
import os
import pathlib
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds for memory-constrained environments:
# Machines with <= 34 GiB total RAM (e.g. 16 GiB / 32 GiB builders/workstations)
# require concurrency throttling during emulator guest execution to prevent
# guest VM eviction, watchdog panics, and snapshot timeouts.
MEMORY_CONSTRAINED_TOTAL_RAM_BYTES = 34 * (1024**3)
# Systems with <= 8 GiB available RAM are considered under active memory pressure.
MEMORY_PRESSURE_AVAILABLE_RAM_BYTES = 8 * (1024**3)
# Systems actively using >= 1 GiB swap are considered swapping/constrained.
MEMORY_PRESSURE_SWAP_USED_BYTES = 1 * (1024**3)


@dataclass
class HostMemoryMetrics:
    """Holds host memory and swap metrics across Linux, macOS, and Windows."""

    total_bytes: int = 0
    available_bytes: int = 0
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)

    @property
    def available_gb(self) -> float:
        return self.available_bytes / (1024**3)

    @property
    def swap_total_gb(self) -> float:
        return self.swap_total_bytes / (1024**3)

    @property
    def swap_used_gb(self) -> float:
        return self.swap_used_bytes / (1024**3)


def _parse_size_with_unit(val_str: str) -> int:
    """Parses strings like '4096.00M', '16G', '16384 kB' into bytes."""
    m = re.match(r"^\s*([0-9.]+)\s*([KkMmGgTt]?)(?:[Bb]|i[Bb])?\s*$", val_str)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
    }
    return int(num * multipliers.get(unit, 1))


def _get_linux_memory_metrics() -> HostMemoryMetrics:
    """Collects memory metrics from /proc/meminfo on Linux."""
    meminfo_path = pathlib.Path("/proc/meminfo")
    if not meminfo_path.exists():
        return HostMemoryMetrics()

    mem_total = 0
    mem_free = 0
    mem_available = 0
    buffers = 0
    cached = 0
    swap_total = 0
    swap_free = 0

    try:
        content = meminfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("Failed to read /proc/meminfo: %s", e)
        return HostMemoryMetrics()

    for line in content.splitlines():
        parts = line.split(":")
        if len(parts) != 2:
            continue
        key, val = parts[0].strip(), parts[1].strip()
        # /proc/meminfo reports values with 'kB' suffix
        if key == "MemTotal":
            mem_total = _parse_size_with_unit(val)
        elif key == "MemFree":
            mem_free = _parse_size_with_unit(val)
        elif key == "MemAvailable":
            mem_available = _parse_size_with_unit(val)
        elif key == "Buffers":
            buffers = _parse_size_with_unit(val)
        elif key == "Cached":
            cached = _parse_size_with_unit(val)
        elif key == "SwapTotal":
            swap_total = _parse_size_with_unit(val)
        elif key == "SwapFree":
            swap_free = _parse_size_with_unit(val)

    if mem_available == 0 and mem_free > 0:
        mem_available = mem_free + buffers + cached

    swap_used = max(0, swap_total - swap_free)
    return HostMemoryMetrics(
        total_bytes=mem_total,
        available_bytes=mem_available,
        swap_total_bytes=swap_total,
        swap_used_bytes=swap_used,
    )


def _get_macos_memory_metrics() -> HostMemoryMetrics:
    """Collects memory and swap metrics on macOS via sysctl and vm_stat."""
    total_bytes = 0
    swap_total_bytes = 0
    swap_used_bytes = 0
    available_bytes = 0

    # 1. Total RAM via sysctl hw.memsize
    try:
        res = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip().isdigit():
            total_bytes = int(res.stdout.strip())
    except Exception as e:  # noqa: BLE001  # Fallback to zero metrics on sysctl failure
        logger.debug("sysctl hw.memsize failed: %s", e)

    # 2. Swap via sysctl vm.swapusage
    # Example output: total = 4096.00M  used = 100.00M  free = 3996.00M  (encrypted)
    try:
        res = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0:
            out = res.stdout.strip()
            total_match = re.search(r"total\s*=\s*([0-9.]+[A-Za-z]*)", out)
            used_match = re.search(r"used\s*=\s*([0-9.]+[A-Za-z]*)", out)
            if total_match:
                swap_total_bytes = _parse_size_with_unit(total_match.group(1))
            if used_match:
                swap_used_bytes = _parse_size_with_unit(used_match.group(1))
    except Exception as e:  # noqa: BLE001  # Fallback to zero metrics on sysctl failure
        logger.debug("sysctl vm.swapusage failed: %s", e)

    # 3. Available RAM estimation via vm_stat
    try:
        res = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0:
            page_size = 4096
            free_pages = 0
            inactive_pages = 0
            speculative_pages = 0
            for line in res.stdout.splitlines():
                if "page size of" in line:
                    m = re.search(r"page size of\s*(\d+)\s*bytes", line)
                    if m:
                        page_size = int(m.group(1))
                elif "Pages free:" in line:
                    m = re.search(r"Pages free:\s*(\d+)", line)
                    if m:
                        free_pages = int(m.group(1))
                elif "Pages inactive:" in line:
                    m = re.search(r"Pages inactive:\s*(\d+)", line)
                    if m:
                        inactive_pages = int(m.group(1))
                elif "Pages speculative:" in line:
                    m = re.search(r"Pages speculative:\s*(\d+)", line)
                    if m:
                        speculative_pages = int(m.group(1))
            available_bytes = (
                free_pages + inactive_pages + speculative_pages
            ) * page_size
    except Exception as e:  # noqa: BLE001  # Fallback to zero metrics on vm_stat failure
        logger.debug("vm_stat failed: %s", e)

    return HostMemoryMetrics(
        total_bytes=total_bytes,
        available_bytes=available_bytes,
        swap_total_bytes=swap_total_bytes,
        swap_used_bytes=swap_used_bytes,
    )


def _get_windows_memory_metrics() -> HostMemoryMetrics:
    """Collects memory metrics on Windows via GlobalMemoryStatusEx."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = stat.ullTotalPhys
            avail = stat.ullAvailPhys
            page_total = stat.ullTotalPageFile
            page_avail = stat.ullAvailPageFile
            swap_total = max(0, page_total - total) if page_total > total else 0
            page_used = max(0, page_total - page_avail)
            phys_used = max(0, total - avail)
            swap_used = max(0, page_used - phys_used) if page_used > phys_used else 0
            return HostMemoryMetrics(
                total_bytes=total,
                available_bytes=avail,
                swap_total_bytes=swap_total,
                swap_used_bytes=swap_used,
            )
    except (AttributeError, OSError) as e:
        logger.debug("Windows GlobalMemoryStatusEx failed: %s", e)
    return HostMemoryMetrics()


def get_host_memory_metrics(env) -> HostMemoryMetrics:
    """Returns memory metrics for the current host environment."""
    try:
        if env.is_macos():
            return _get_macos_memory_metrics()
        elif env.is_windows():
            return _get_windows_memory_metrics()
        elif env.is_linux():
            return _get_linux_memory_metrics()
    except Exception as e:  # noqa: BLE001  # Fallback to empty metrics on host query failure
        logger.warning("Failed to collect host memory metrics: %s", e)
    return HostMemoryMetrics()


def is_memory_constrained(env, metrics: HostMemoryMetrics | None = None) -> bool:
    """Determines whether the host is memory-constrained for Bazel flag configuration.

    To preserve Bazel remote cache hermeticity and prevent flag instability from
    volatile background processes, this check relies strictly on static host properties:
      - Explicit override via AEMU_CONSTRAIN_MEMORY or BUILD_MEMORY_PRESSURE.
      - Machines with total physical RAM <= 34 GiB (e.g. 16 GiB macOS CI workers
        or 32 GiB workstations).
      - Fallback to True on macOS only if physical RAM metrics cannot be read.
    """
    override = os.environ.get("AEMU_CONSTRAIN_MEMORY") or os.environ.get(
        "BUILD_MEMORY_PRESSURE"
    )
    if override is not None:
        return override.lower() in ("1", "true", "yes", "on")

    if metrics is None:
        metrics = get_host_memory_metrics(env)

    # Static physical RAM check: <= 34 GiB systems require throttling, while
    # > 34 GiB systems (including 64+ GiB Macs and Linux workstations) do not.
    if metrics.total_bytes > 0:
        return metrics.total_bytes <= MEMORY_CONSTRAINED_TOTAL_RAM_BYTES

    # Fallback when total_bytes could not be determined (e.g. sysctl unavailable)
    return bool(env.is_macos())


def get_jvm_heap_limit(env, metrics: HostMemoryMetrics | None = None) -> str:
    """Returns a bounded JVM heap size scaled to host physical memory.

    For small 8 GiB hosts (such as 8 GiB Mac runners), caps JVM heap to '1g'
    to leave physical memory available for the emulator guest VM.
    For 16 GiB hosts, allocates '2g'.
    For 32 GiB hosts, allocates '4g'.
    For larger constrained hosts, scales up to '16g'.
    """
    if metrics is None:
        if hasattr(env, "get_host_memory_metrics"):
            res = env.get_host_memory_metrics()
            metrics = res if isinstance(res, HostMemoryMetrics) else HostMemoryMetrics()
        else:
            metrics = get_host_memory_metrics(env)

    if metrics.total_bytes > 0:
        total_gb = metrics.total_gb
        if total_gb <= 8.5:
            return "1g"
        elif total_gb <= 16.5:
            return "2g"
        elif total_gb <= 34:
            return "4g"
        else:
            heap_gb = max(4, min(16, int(total_gb // 8)))
            return f"{heap_gb}g"

    # Fallback when metrics cannot be determined: 1g on macOS (matching small macs), 4g otherwise
    if env.is_macos():
        return "1g"
    return "4g"


def log_host_memory_status(env, label: str = "") -> None:
    """Logs host memory, swap metrics, and constraint state for diagnostics."""
    suffix = f" [{label}]" if label else ""
    try:
        if env.is_macos():
            swap_res = subprocess.run(
                ["sysctl", "-n", "vm.swapusage"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            swap_info = (
                swap_res.stdout.strip() if swap_res.returncode == 0 else "unknown"
            )
            vm_res = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            logger.info("Host memory status%s (macOS): swap=[%s]", suffix, swap_info)
            if vm_res.returncode == 0:
                lines = [
                    line.strip() for line in vm_res.stdout.splitlines() if line.strip()
                ]
                logger.info("Host vm_stat%s: %s", suffix, "; ".join(lines[:7]))
        elif env.is_linux():
            meminfo_path = pathlib.Path("/proc/meminfo")
            if meminfo_path.exists():
                metrics = {}
                for line in meminfo_path.read_text().splitlines():
                    parts = line.split(":")
                    if len(parts) == 2 and parts[0] in (
                        "MemTotal",
                        "MemFree",
                        "MemAvailable",
                        "SwapTotal",
                        "SwapFree",
                    ):
                        metrics[parts[0]] = parts[1].strip()
                logger.info("Host memory status%s (Linux): %s", suffix, metrics)
        elif env.is_windows():
            m = get_host_memory_metrics(env)
            logger.info(
                "Host memory status%s (Windows): total=%.2f GiB, available=%.2f GiB, swap_used=%.2f GiB",
                suffix,
                m.total_gb,
                m.available_gb,
                m.swap_used_gb,
            )
    except Exception as e:  # noqa: BLE001  # Diagnostic telemetry must not crash the build
        logger.warning("Failed to collect host memory status%s: %s", suffix, e)


# Backwards compatibility alias
_log_host_memory_status = log_host_memory_status
