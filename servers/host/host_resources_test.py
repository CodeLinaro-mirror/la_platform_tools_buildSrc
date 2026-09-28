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

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SERVERS_DIR = Path(__file__).resolve().parent.parent
if str(_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVERS_DIR))

from host import host_resources


class HostResourcesTest(unittest.TestCase):

    def setUp(self):
        self.mock_env = MagicMock()
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_windows.return_value = False
        self.mock_env.is_linux.return_value = True

    def test_parse_size_with_unit(self):
        self.assertEqual(host_resources._parse_size_with_unit("1024"), 1024)
        self.assertEqual(host_resources._parse_size_with_unit("16384 kB"), 16384 * 1024)
        self.assertEqual(
            host_resources._parse_size_with_unit("4096.00M"), 4096 * 1024 * 1024
        )
        self.assertEqual(
            host_resources._parse_size_with_unit("16G"), 16 * 1024 * 1024 * 1024
        )
        self.assertEqual(host_resources._parse_size_with_unit("invalid"), 0)

    @patch("host.host_resources.pathlib.Path.exists")
    @patch("host.host_resources.pathlib.Path.read_text")
    def test_get_linux_memory_metrics(self, mock_read_text, mock_exists):
        mock_exists.return_value = True
        mock_read_text.return_value = (
            "MemTotal:       102830828 kB\n"
            "MemFree:        84920104 kB\n"
            "MemAvailable:   95123456 kB\n"
            "SwapTotal:       4194300 kB\n"
            "SwapFree:        4194300 kB\n"
        )
        metrics = host_resources._get_linux_memory_metrics()
        self.assertAlmostEqual(metrics.total_gb, 102830828 * 1024 / (1024**3), places=2)
        self.assertAlmostEqual(
            metrics.available_gb, 95123456 * 1024 / (1024**3), places=2
        )
        self.assertEqual(metrics.swap_used_bytes, 0)

    @patch("host.host_resources.pathlib.Path.exists")
    @patch("host.host_resources.pathlib.Path.read_text")
    def test_get_linux_memory_metrics_fallback_available(
        self, mock_read_text, mock_exists
    ):
        mock_exists.return_value = True
        mock_read_text.return_value = (
            "MemTotal:       16777216 kB\n"
            "MemFree:         4194304 kB\n"
            "Buffers:         1048576 kB\n"
            "Cached:          2097152 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
        )
        metrics = host_resources._get_linux_memory_metrics()
        expected_avail = (4194304 + 1048576 + 2097152) * 1024
        self.assertEqual(metrics.available_bytes, expected_avail)
        self.assertEqual(metrics.swap_used_bytes, 1048576 * 1024)

    @patch("host.host_resources.pathlib.Path.exists")
    def test_get_linux_memory_metrics_missing_file(self, mock_exists):
        mock_exists.return_value = False
        metrics = host_resources._get_linux_memory_metrics()
        self.assertEqual(metrics.total_bytes, 0)
        self.assertEqual(metrics.available_bytes, 0)

    @patch("host.host_resources.subprocess.run")
    def test_get_macos_memory_metrics(self, mock_run):
        mock_run.side_effect = [
            # sysctl hw.memsize
            MagicMock(returncode=0, stdout="17179869184\n"),
            # sysctl vm.swapusage
            MagicMock(
                returncode=0,
                stdout="total = 4096.00M  used = 150.00M  free = 3946.00M  (encrypted)\n",
            ),
            # vm_stat
            MagicMock(
                returncode=0,
                stdout=(
                    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                    "Pages free: 100000.\n"
                    "Pages active: 200000.\n"
                    "Pages inactive: 50000.\n"
                    "Pages speculative: 10000.\n"
                ),
            ),
        ]
        metrics = host_resources._get_macos_memory_metrics()
        self.assertEqual(metrics.total_bytes, 17179869184)
        self.assertEqual(metrics.total_gb, 16.0)
        self.assertEqual(metrics.swap_total_bytes, 4096 * 1024 * 1024)
        self.assertEqual(metrics.swap_used_bytes, 150 * 1024 * 1024)
        self.assertEqual(metrics.available_bytes, (100000 + 50000 + 10000) * 16384)

    @patch("host.host_resources.subprocess.run")
    def test_get_macos_memory_metrics_handles_failure(self, mock_run):
        mock_run.side_effect = Exception("sysctl missing")
        metrics = host_resources._get_macos_memory_metrics()
        self.assertEqual(metrics.total_bytes, 0)
        self.assertEqual(metrics.available_bytes, 0)

    def test_is_memory_constrained_low_total_ram(self):
        # 16 GiB machine -> constrained
        metrics = host_resources.HostMemoryMetrics(
            total_bytes=16 * (1024**3),
            available_bytes=12 * (1024**3),
            swap_total_bytes=0,
            swap_used_bytes=0,
        )
        self.assertTrue(host_resources.is_memory_constrained(self.mock_env, metrics))

        # 32 GiB machine -> constrained
        metrics_32g = host_resources.HostMemoryMetrics(
            total_bytes=32 * (1024**3),
            available_bytes=24 * (1024**3),
            swap_total_bytes=0,
            swap_used_bytes=0,
        )
        self.assertTrue(
            host_resources.is_memory_constrained(self.mock_env, metrics_32g)
        )

    def test_is_memory_constrained_high_total_ram_unconstrained(self):
        # 102 GiB builder -> unconstrained
        metrics = host_resources.HostMemoryMetrics(
            total_bytes=102 * (1024**3),
            available_bytes=90 * (1024**3),
            swap_total_bytes=4 * (1024**3),
            swap_used_bytes=0,
        )
        self.assertFalse(host_resources.is_memory_constrained(self.mock_env, metrics))

    def test_is_memory_constrained_macos_scales_with_ram(self):
        # 16 GiB Mac mini -> constrained
        self.mock_env.is_macos.return_value = True
        metrics_16g = host_resources.HostMemoryMetrics(
            total_bytes=16 * (1024**3),
            available_bytes=14 * (1024**3),
        )
        self.assertTrue(
            host_resources.is_memory_constrained(self.mock_env, metrics_16g)
        )

        # 64 GiB Mac Studio -> unconstrained when physical RAM > 34 GiB
        metrics_64g = host_resources.HostMemoryMetrics(
            total_bytes=64 * (1024**3),
            available_bytes=50 * (1024**3),
        )
        self.assertFalse(
            host_resources.is_memory_constrained(self.mock_env, metrics_64g)
        )

    def test_get_jvm_heap_limit(self):
        # Small 8 GiB macs must use 1g heap
        metrics_8g = host_resources.HostMemoryMetrics(total_bytes=8 * (1024**3))
        self.assertEqual(
            host_resources.get_jvm_heap_limit(self.mock_env, metrics_8g), "1g"
        )

        # 16 GiB hosts use 2g heap
        metrics_16g = host_resources.HostMemoryMetrics(total_bytes=16 * (1024**3))
        self.assertEqual(
            host_resources.get_jvm_heap_limit(self.mock_env, metrics_16g), "2g"
        )

        # 32 GiB hosts use 4g heap
        metrics_32g = host_resources.HostMemoryMetrics(total_bytes=32 * (1024**3))
        self.assertEqual(
            host_resources.get_jvm_heap_limit(self.mock_env, metrics_32g), "4g"
        )

        # 64 GiB hosts use 8g heap
        metrics_64g = host_resources.HostMemoryMetrics(total_bytes=64 * (1024**3))
        self.assertEqual(
            host_resources.get_jvm_heap_limit(self.mock_env, metrics_64g), "8g"
        )

        # 256 GiB hosts use 16g heap
        metrics_256g = host_resources.HostMemoryMetrics(total_bytes=256 * (1024**3))
        self.assertEqual(
            host_resources.get_jvm_heap_limit(self.mock_env, metrics_256g), "16g"
        )

        # Fallbacks when metrics are empty: macOS defaults to 1g (matching small macs), Linux to 4g
        empty = host_resources.HostMemoryMetrics()
        self.mock_env.is_macos.return_value = True
        self.assertEqual(host_resources.get_jvm_heap_limit(self.mock_env, empty), "1g")
        self.mock_env.is_macos.return_value = False
        self.assertEqual(host_resources.get_jvm_heap_limit(self.mock_env, empty), "4g")

    def test_is_memory_constrained_hermetic_unaffected_by_volatile_ram(self):
        # 102 GiB Linux builder is unconstrained regardless of volatile available RAM fluctuations
        self.mock_env.is_macos.return_value = False
        metrics_high_load = host_resources.HostMemoryMetrics(
            total_bytes=102 * (1024**3),
            available_bytes=4
            * (1024**3),  # Temporary low available RAM from background job
            swap_total_bytes=4 * (1024**3),
            swap_used_bytes=2 * (1024**3),
        )
        # Must return False to preserve Bazel cache hermeticity and RBE concurrency
        self.assertFalse(
            host_resources.is_memory_constrained(self.mock_env, metrics_high_load)
        )

    def test_is_memory_constrained_env_override(self):
        metrics = host_resources.HostMemoryMetrics(
            total_bytes=102 * (1024**3),
            available_bytes=90 * (1024**3),
        )
        with patch.dict(os.environ, {"AEMU_CONSTRAIN_MEMORY": "1"}):
            self.assertTrue(
                host_resources.is_memory_constrained(self.mock_env, metrics)
            )

        metrics_low = host_resources.HostMemoryMetrics(
            total_bytes=16 * (1024**3),
            available_bytes=4 * (1024**3),
        )
        with patch.dict(os.environ, {"AEMU_CONSTRAIN_MEMORY": "0"}):
            self.assertFalse(
                host_resources.is_memory_constrained(self.mock_env, metrics_low)
            )

    def test_is_memory_constrained_fallback_on_metrics_failure(self):
        empty_metrics = host_resources.HostMemoryMetrics()
        self.mock_env.is_macos.return_value = True
        self.assertTrue(
            host_resources.is_memory_constrained(self.mock_env, empty_metrics)
        )

        self.mock_env.is_macos.return_value = False
        self.assertFalse(
            host_resources.is_memory_constrained(self.mock_env, empty_metrics)
        )

    @patch("host.host_resources.subprocess.run")
    def test_log_host_memory_status_macos(self, mock_run):
        self.mock_env.is_macos.return_value = True
        self.mock_env.is_linux.return_value = False
        mock_run.side_effect = [
            MagicMock(
                returncode=0, stdout="total = 4096.00M used = 100.00M free = 3996.00M"
            ),
            MagicMock(
                returncode=0,
                stdout="Mach Virtual Memory Statistics:\nPages free: 1000.\nPages active: 2000.\n",
            ),
        ]
        with self.assertLogs(level="INFO") as cm:
            host_resources.log_host_memory_status(self.mock_env, "before shutdown")
        self.assertTrue(
            any(
                "Host memory status [before shutdown] (macOS)" in line
                for line in cm.output
            )
        )
        self.assertTrue(
            any("Host vm_stat [before shutdown]" in line for line in cm.output)
        )

    @patch("host.host_resources.pathlib.Path.exists")
    @patch("host.host_resources.pathlib.Path.read_text")
    def test_log_host_memory_status_linux(self, mock_read_text, mock_exists):
        self.mock_env.is_macos.return_value = False
        self.mock_env.is_linux.return_value = True
        mock_exists.return_value = True
        mock_read_text.return_value = (
            "MemTotal:        16384 kB\n"
            "MemFree:          8192 kB\n"
            "MemAvailable:    12000 kB\n"
            "SwapTotal:        4096 kB\n"
            "SwapFree:         2048 kB\n"
        )
        with self.assertLogs(level="INFO") as cm:
            host_resources.log_host_memory_status(self.mock_env, "after shutdown")
        self.assertTrue(
            any(
                "Host memory status [after shutdown] (Linux)" in line
                for line in cm.output
            )
        )
        self.assertTrue(any("'MemTotal': '16384 kB'" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
