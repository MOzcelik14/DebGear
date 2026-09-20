"""Minimal GTK smoke test under xvfb: construct and navigate the actual window."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from unittest.mock import patch
import debgear


def main():
    Adw.init()
    app = debgear.DriverManager()
    app.register(None)
    with (
        patch.object(debgear, "operating_system", return_value={
            "ID": "linuxmint", "PRETTY_NAME": "Linux Mint 22.3",
        }),
        patch.object(debgear.DriverWindow, "start_refresh"),
    ):
        window = debgear.DriverWindow(application=app)
    assert not window.supported_host
    assert window.stack.get_visible_child_name() == "overview"
    for section in (
        "graphics", "network", "firmware", "kernel", "activity", "overview"
    ):
        window.select_page(None, section)
        assert window.stack.get_visible_child_name() == section
    window._apply_refresh([], None, {
        "gpu": {"available": False, "reason": "No NVIDIA GPU"},
        "firmware": {"state": "missing", "message": "No fwupd"},
        "messages": {"state": "unavailable", "message": "No kernel access"},
        "kernel": {"release": "test-kernel", "headers": False,
                   "dkms": {"state": "missing", "text": "No DKMS"},
                   "secure_boot": "Unknown"},
    })
    assert len(window.devices) == 0
    assert not window.apply_button.get_sensitive()
    window.destroy()
    print("GTK smoke test passed: six pages, empty-state and Mint read-only mode")


if __name__ == "__main__":
    main()
