"""Headless regression coverage for Debian driver decisions and safety guards."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

# Import the Python backend without requiring a graphical session in CI.
gi = types.ModuleType("gi")
gi.require_version = lambda *_args: None
repository = types.ModuleType("gi.repository")
repository.Gtk = types.SimpleNamespace()
repository.Adw = types.SimpleNamespace(
    ApplicationWindow=type("ApplicationWindow", (), {}),
    Application=type("Application", (), {}),
)
repository.Gio = types.SimpleNamespace()
repository.Gdk = types.SimpleNamespace()
repository.GLib = types.SimpleNamespace()
gi.repository = repository
sys.modules["gi"] = gi
sys.modules["gi.repository"] = repository

spec = importlib.util.spec_from_file_location(
    "debgear", Path(__file__).resolve().parents[1] / "debgear.py"
)
debgear = importlib.util.module_from_spec(spec)
spec.loader.exec_module(debgear)


class DriverDecisionTests(unittest.TestCase):
    def test_invalid_version_is_not_reported_as_equal(self):
        with patch.object(debgear, "run_command", return_value=(2, "bad version")):
            self.assertIsNone(debgear.version_compare("invalid?", "1.0"))
            self.assertFalse(debgear.version_is_newer("invalid?", "1.0"))

    def test_exact_version_is_equal_without_running_dpkg(self):
        with patch.object(debgear, "run_command") as run:
            self.assertEqual(debgear.version_compare("550.163.01-2", "550.163.01-2"), 0)
            run.assert_not_called()

    def test_nvidia_update_uses_candidate_not_backports_maximum(self):
        pkg = {
            "installed": True,
            "installed_version": "550.163.01-2",
            "candidate_version": "550.163.01-2",
            "newest_version": "580.10-1~bpo13+1",
            "source": "Backports",
        }
        with (
            patch.object(debgear, "get_pkg_info", return_value=pkg),
            patch.object(debgear, "is_nvidia_module_loaded", return_value=True),
            patch.object(debgear, "check_nvidia_smi", return_value=(True, "NVIDIA-SMI")),
            patch.object(debgear, "get_installed_package_version", return_value=(True, "headers")),
            patch.object(debgear.shutil, "which", return_value=None),
        ):
            status = debgear.get_nvidia_status("nvidia")
        self.assertTrue(status["nvidia_smi"])
        self.assertFalse(status["has_update"])
        self.assertEqual(status["newest_version"], "580.10-1~bpo13+1")

    def test_scan_caches_nvidia_package_for_multiple_devices(self):
        lspci = """0000:00:02.0 VGA compatible controller [0300]: Intel UHD [8086:46a3]
    Kernel driver in use: i915
0000:01:00.0 3D controller [0302]: NVIDIA GA107 [10de:25a5]
    Kernel driver in use: nvidia
0000:02:00.0 3D controller [0302]: NVIDIA GA108 [10de:25b0]
    Kernel driver in use: nvidia
"""
        pkg = dict(
            installed=True, installed_version="550", candidate_version="550",
            newest_version="550", source="APT repository",
        )
        with (
            patch.object(debgear, "command_output", return_value=lspci),
            patch.object(debgear, "get_pkg_info", return_value=pkg) as info,
        ):
            devices = debgear.scan_hardware()
        self.assertEqual(len(devices), 3)
        self.assertEqual(sum(d["pkg"] == "nvidia-driver" for d in devices), 2)
        self.assertEqual(info.call_count, 2)
        self.assertEqual(len({d["device_id"] for d in devices}), 3)


class PrivilegedOperationTests(unittest.TestCase):
    def setUp(self):
        self.result = []
        self.window = types.SimpleNamespace(
            operation_finished=lambda success, output: self.result.append((success, output)),
            _report_operation=lambda _message: None,
        )

    def test_removal_refuses_additional_packages(self):
        plan = "Remv nvidia-driver [550]\nRemv gnome-shell [46]"
        with (
            patch.object(debgear, "get_installed_package_version", return_value=(True, "550")),
            patch.object(debgear, "run_command", return_value=(0, plan)) as run,
            patch.object(debgear.GLib, "idle_add",
                         side_effect=lambda callback, *args: callback(*args)),
        ):
            debgear.DriverWindow.execute_package_changes(
                self.window, [("remove", "nvidia-driver")]
            )
        self.assertFalse(self.result[0][0])
        self.assertIn("gnome-shell", self.result[0][1])
        self.assertEqual(run.call_count, 1)  # simulate only; never pkexec

    def test_repair_aborts_without_matching_headers(self):
        with (
            patch.object(debgear, "get_installed_package_version", return_value=(False, "Not installed")),
            patch.object(debgear, "get_apt_candidate", return_value="Unknown"),
            patch.object(debgear, "run_command") as run,
            patch.object(debgear.GLib, "idle_add",
                         side_effect=lambda callback, *args: callback(*args)),
        ):
            debgear.DriverWindow.execute_nvidia_repair(self.window)
        self.assertFalse(self.result[0][0])
        self.assertIn("matching headers", self.result[0][1])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
