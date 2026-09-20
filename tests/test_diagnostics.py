"""Tests for read-only DebGear diagnostics and local action history."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import debgear_diagnostics as diag


class DistributionTests(unittest.TestCase):
    def test_debian_enables_package_actions(self):
        self.assertTrue(diag.supports_driver_changes({"ID": "debian"}))
        for name in ("linuxmint", "ubuntu", "pop", ""):
            with self.subTest(name=name):
                self.assertFalse(diag.supports_driver_changes({"ID": name}))

    def test_detects_mint_nvidia_metapackage(self):
        output = (
            "nvidia-driver-580\t580.95\tinstalled\n"
            "nvidia-driver-550\t550.1\tnot-installed\n"
            "nvidia-settings\t580.95\tinstalled\n"
        )
        with patch.object(diag, "run_probe", return_value=(0, output)):
            self.assertEqual(diag.detect_nvidia_package(), "nvidia-driver-580")

    def test_prefers_debian_nvidia_metapackage(self):
        output = (
            "nvidia-driver\t550.163.01\tinstalled\n"
            "nvidia-driver-580\t580.95\tinstalled"
        )
        with patch.object(diag, "run_probe", return_value=(0, output)):
            self.assertEqual(diag.detect_nvidia_package(), "nvidia-driver")


class TelemetryTests(unittest.TestCase):
    def test_parses_nvidia_telemetry(self):
        output = "NVIDIA GeForce RTX 3050, 580.95, 4096, 52, 23, 37.4"
        with (
            patch.object(diag.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(diag, "run_probe", return_value=(0, output)),
        ):
            result = diag.query_gpu()
        self.assertTrue(result["available"])
        self.assertEqual(result["devices"][0]["temperature.gpu"], "52")
        self.assertEqual(result["devices"][0]["memory.total"], "4096")

    def test_fwupd_inventory_does_not_guess_update_availability(self):
        payload = json.dumps({"Devices": [
            {"Name": "System Firmware", "Version": "1.0", "Vendor": "Example"}
        ]})
        with (
            patch.object(diag.shutil, "which", return_value="/usr/bin/fwupdmgr"),
            patch.object(diag, "run_probe", return_value=(0, payload)),
        ):
            result = diag.firmware_report()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["devices"][0]["version"], "1.0")
        self.assertNotIn("updates", result)

    def test_inaccessible_kernel_journal_is_not_misreported_as_clean(self):
        with (
            patch.object(diag.shutil, "which", return_value="/usr/bin/journalctl"),
            patch.object(diag, "run_probe", return_value=(1, "Access denied")),
        ):
            result = diag.firmware_messages()
        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["messages"])


class HistoryTests(unittest.TestCase):
    def test_history_is_private_and_only_stores_action_result(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"XDG_STATE_HOME": temp}):
                diag.record_operation("Rebuild NVIDIA", False)
                items = diag.read_history()
                self.assertEqual(items[0]["action"], "Rebuild NVIDIA")
                self.assertFalse(items[0]["success"])
                self.assertEqual(diag.history_path().stat().st_mode & 0o777, 0o600)
                self.assertNotIn("logs", items[0])


if __name__ == "__main__":
    unittest.main()
