#!/usr/bin/env python3

import gi
import subprocess
import sys
import re
import threading
import os

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, Gdk, GLib


# ============================================================
# DRIVER DEFINITIONS
# ============================================================

DRIVER_MAP = {
    "10de": {
        "pkg": "nvidia-driver",
        "name": "NVIDIA Proprietary Driver",
        "desc": "Closed-source driver for NVIDIA graphics hardware."
    },

    "8086": {
        "pkg": None,
        "name": "Intel Open Source Driver",
        "desc": "Intel graphics hardware is supported by the Linux kernel and Mesa."
    },

    "1002": {
        "pkg": None,
        "name": "AMD Open Source Driver",
        "desc": "AMD graphics hardware is supported by the Linux kernel and Mesa."
    }
}


# ============================================================
# COMMAND HELPERS
# ============================================================

def run_command(command, env_extra=None):

    env = os.environ.copy()

    if env_extra:
        env.update(env_extra)

    try:

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        return (
            result.returncode,
            result.stdout.strip()
        )

    except Exception as exc:

        return (
            1,
            str(exc)
        )


def command_output(command, env_extra=None):

    code, output = run_command(
        command,
        env_extra
    )

    if code == 0:
        return output

    return ""


# ============================================================
# VERSION COMPARISON
# ============================================================

def version_compare(version_a, version_b):

    """
    Debian version comparison.

    return:
       1  -> a > b
       0  -> a == b
      -1  -> a < b
    """

    if not version_a or not version_b:
        return 0

    code, _ = run_command([
        "dpkg",
        "--compare-versions",
        version_a,
        "gt",
        version_b
    ])

    if code == 0:
        return 1

    code, _ = run_command([
        "dpkg",
        "--compare-versions",
        version_a,
        "lt",
        version_b
    ])

    if code == 0:
        return -1

    return 0


def version_is_newer(candidate, installed):

    return (
        version_compare(
            candidate,
            installed
        ) > 0
    )


# ============================================================
# PACKAGE INSTALLED VERSION
# ============================================================

def get_installed_package_version(package):

    code, status = run_command([
        "dpkg-query",
        "-W",
        "-f=${db:Status-Status}",
        package
    ])

    if (
        code != 0
        or status.strip() != "installed"
    ):

        return (
            False,
            "Not installed"
        )

    code, version = run_command([
        "dpkg-query",
        "-W",
        "-f=${Version}",
        package
    ])

    if code != 0 or not version:

        return (
            True,
            "Unknown"
        )

    return (
        True,
        version.strip()
    )


# ============================================================
# APT CANDIDATE
# ============================================================

def get_apt_candidate(package):

    """
    Gets the Candidate version from apt-cache policy.

    LC_ALL=C is used so the system language does not matter.
    """

    code, output = run_command(
        [
            "apt-cache",
            "policy",
            package
        ],
        {
            "LC_ALL": "C",
            "LANG": "C"
        }
    )

    if code != 0:
        return "Unknown"

    for line in output.splitlines():

        line = line.strip()

        if line.startswith("Candidate:"):

            version = line.split(
                ":",
                1
            )[1].strip()

            if (
                version
                and version != "(none)"
            ):

                return version

    return "Unknown"


# ============================================================
# APT ALL AVAILABLE VERSIONS
# ============================================================

def get_apt_versions(package):

    """
    Collects all versions known by APT using apt-cache madison.

    Example:

    nvidia-driver |
    550.163.01-4~bpo13+1 |
    http://deb.debian.org/... trixie-backports/non-free amd64 Packages

    nvidia-driver |
    550.163.01-2 |
    http://deb.debian.org/... trixie/non-free amd64 Packages
    """

    code, output = run_command(
        [
            "apt-cache",
            "madison",
            package
        ],
        {
            "LC_ALL": "C",
            "LANG": "C"
        }
    )

    if code != 0:
        return []

    versions = []

    for line in output.splitlines():

        parts = [
            x.strip()
            for x in line.split("|")
        ]

        if len(parts) < 2:
            continue

        version = parts[1]

        if not version:
            continue

        if version not in versions:
            versions.append(
                version
            )

    return versions


# ============================================================
# FIND NEWEST APT VERSION
# ============================================================

def get_newest_apt_version(package):

    versions = get_apt_versions(
        package
    )

    if not versions:
        return "Unknown"

    newest = versions[0]

    for version in versions[1:]:

        if version_compare(
            version,
            newest
        ) > 0:

            newest = version

    return newest


# ============================================================
# DETECT VERSION SOURCE
# ============================================================

def get_version_source(
    package,
    version
):

    """
    Attempts to determine which APT repository
    provides the specified version.
    """

    if not version:
        return "Unknown"

    code, output = run_command(
        [
            "apt-cache",
            "madison",
            package
        ],
        {
            "LC_ALL": "C",
            "LANG": "C"
        }
    )

    if code != 0:
        return "Unknown"

    for line in output.splitlines():

        parts = [
            x.strip()
            for x in line.split("|")
        ]

        if len(parts) < 3:
            continue

        found_version = parts[1]
        repository = parts[2]

        if found_version != version:
            continue

        repo = repository.lower()

        if "backports" in repo:
            return "Backports"

        if "trixie" in repo:
            return "Debian Trixie"

        if "testing" in repo:
            return "Debian Testing"

        if "sid" in repo:
            return "Debian Sid"

        return "APT Repository"

    return "Unknown"


# ============================================================
# PACKAGE INFORMATION
# ============================================================

def get_pkg_info(package):

    if not package:

        return {
            "installed": True,
            "installed_version": "Provided by the system",
            "candidate_version": "Provided by the system",
            "newest_version": "Provided by the system",
            "source": "Kernel / Mesa"
        }

    installed, installed_version = (
        get_installed_package_version(
            package
        )
    )

    candidate_version = (
        get_apt_candidate(
            package
        )
    )

    newest_version = (
        get_newest_apt_version(
            package
        )
    )

    source = get_version_source(
        package,
        newest_version
    )

    return {
        "installed": installed,
        "installed_version": installed_version,
        "candidate_version": candidate_version,
        "newest_version": newest_version,
        "source": source
    }


# ============================================================
# NVIDIA KERNEL DRIVER
# ============================================================

def get_nvidia_kernel_driver():

    output = command_output([
        "lspci",
        "-nnk"
    ])

    if not output:
        return None

    current_nvidia = False

    for line in output.splitlines():

        if line and not line.startswith(
            (" ", "\t")
        ):

            lower = line.lower()

            is_nvidia = (
                "[10de:" in lower
            )

            is_gpu = any(
                keyword in lower
                for keyword in (
                    "vga compatible controller",
                    "3d controller",
                    "display controller"
                )
            )

            current_nvidia = (
                is_nvidia
                and is_gpu
            )

            continue

        if current_nvidia:

            match = re.search(
                r"Kernel driver in use:\s*(.+)",
                line
            )

            if match:
                return match.group(1).strip()

    return None


# ============================================================
# NVIDIA MODULE
# ============================================================

def is_nvidia_module_loaded():

    output = command_output([
        "lsmod"
    ])

    for line in output.splitlines():

        parts = line.split()

        if (
            parts
            and parts[0] == "nvidia"
        ):

            return True

    return False


# ============================================================
# NVIDIA-SMI
# ============================================================

def check_nvidia_smi():

    code, output = run_command([
        "nvidia-smi"
    ])

    if code != 0:

        return (
            False,
            output
        )

    return (
        "NVIDIA-SMI" in output,
        output
    )


# ============================================================
# NVIDIA STATUS
#
# NOTE (fix): this is the single, authoritative place that computes
# NVIDIA status for a refresh cycle. It used to be called up to 3
# times per refresh (once in create_device_card, once again inside
# create_status_widget, once more in update_global_status), each
# time re-running lspci/lsmod/nvidia-smi/apt-cache. Now it is called
# exactly once per refresh, cached, and the cached dict is threaded
# through to whoever needs it.
# ============================================================

def get_nvidia_status():

    package = get_pkg_info(
        "nvidia-driver"
    )

    kernel_driver = (
        get_nvidia_kernel_driver()
    )

    module_loaded = (
        is_nvidia_module_loaded()
    )

    nvidia_smi, smi_output = (
        check_nvidia_smi()
    )

    # --------------------------------------------------------
    # NVIDIA-SMI is the most reliable user-space check.
    # If it works, the NVIDIA driver is considered active.
    # --------------------------------------------------------

    if nvidia_smi:

        status = "active"

    elif kernel_driver == "nvidia":

        status = "kernel-active"

    elif kernel_driver == "nouveau":

        status = "nouveau"

    elif module_loaded:

        status = "module-loaded"

    elif package["installed"]:

        status = "installed-not-active"

    else:

        status = "not-installed"

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    newest = package[
        "newest_version"
    ]

    installed = package[
        "installed_version"
    ]

    has_update = False

    if (
        package["installed"]
        and newest != "Unknown"
        and installed != "Unknown"
    ):

        has_update = version_is_newer(
            newest,
            installed
        )

    return {
        "installed": package["installed"],
        "installed_version": installed,
        "candidate_version": package[
            "candidate_version"
        ],
        "newest_version": newest,
        "source": package["source"],
        "kernel_driver": kernel_driver,
        "module_loaded": module_loaded,
        "nvidia_smi": nvidia_smi,
        "status": status,
        "has_update": has_update,
        "smi_output": smi_output
    }


# ============================================================
# HARDWARE SCANNER
# ============================================================

def scan_hardware():

    output = command_output([
        "lspci",
        "-nnk"
    ])

    if not output:
        return []

    devices = []

    current = None

    for line in output.splitlines():

        # ----------------------------------------------------
        # PCI DEVICE
        # ----------------------------------------------------

        if line and not line.startswith(
            (" ", "\t")
        ):

            match = re.search(
                r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]",
                line
            )

            if not match:

                current = None
                continue

            vendor = (
                match.group(1)
                .lower()
            )

            device_id = (
                match.group(2)
                .lower()
            )

            lower = line.lower()

            is_gpu = any(
                x in lower
                for x in (
                    "vga compatible controller",
                    "3d controller",
                    "display controller"
                )
            )

            is_network = (
                "network controller" in lower
                or "ethernet controller" in lower
            )

            if (
                not is_gpu
                and not is_network
            ):

                current = None
                continue

            # Clean PCI class information
            name = re.sub(
                r"^[0-9a-fA-F:.]+\s+",
                "",
                line
            )

            # Remove [vendor:device] part
            name = re.sub(
                r"\s*\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]",
                "",
                name
            )

            name = name.strip()

            current = {
                "name": name,
                "vendor": vendor,
                "device": device_id,
                "type": (
                    "gpu"
                    if is_gpu
                    else "network"
                ),
                "kernel": None
            }

            devices.append(
                current
            )

            continue

        # ----------------------------------------------------
        # KERNEL DRIVER
        # ----------------------------------------------------

        if current:

            match = re.search(
                r"Kernel driver in use:\s*(.+)",
                line
            )

            if match:

                current["kernel"] = (
                    match.group(1).strip()
                )

    # ========================================================
    # RESULT
    # ========================================================

    result = []

    for index, device in enumerate(devices):

        vendor = device[
            "vendor"
        ]

        # ----------------------------------------------------
        # NETWORK
        # ----------------------------------------------------

        if device["type"] == "network":

            lower_name = (
                device["name"]
                .lower()
            )

            if any(
                x in lower_name
                for x in (
                    "wifi",
                    "wireless",
                    "wi-fi",
                    "wlan"
                )
            ):

                driver_name = (
                    "Kernel Wi-Fi Driver"
                )

            else:

                driver_name = (
                    "Kernel Network Driver"
                )

            info = {
                "pkg": None,
                "name": driver_name,
                "desc": (
                    "Network hardware is supported "
                    "by the Linux kernel."
                )
            }

        # ----------------------------------------------------
        # GPU
        # ----------------------------------------------------

        else:

            info = DRIVER_MAP.get(
                vendor,
                {
                    "pkg": None,
                    "name": "Kernel / Mesa Driver",
                    "desc": (
                        "Graphics hardware is supported "
                        "by the Linux kernel and Mesa."
                    )
                }
            )

        package_info = get_pkg_info(
            info["pkg"]
        )

        result.append({
            # NOTE (fix): a stable per-device id, independent of the
            # package name. Two NVIDIA GPUs would both have
            # pkg == "nvidia-driver"; using the package name as the
            # switches{} key silently dropped the first card's
            # switch. "device_id" is unique per scan and used for
            # the switch map instead, while "pkg" is kept separately
            # for the actual install/remove action.
            "device_id": index,
            "name": device["name"],
            "vendor": vendor,
            "device": device["device"],
            "type": device["type"],
            "pkg": info["pkg"],
            "driver_name": info["name"],
            "desc": info["desc"],
            "kernel": (
                device["kernel"]
                or "Not loaded"
            ),
            **package_info
        })

    return result


# ============================================================
# MAIN WINDOW
# ============================================================

class DriverWindow(
    Adw.ApplicationWindow
):

    def __init__(self, **kwargs):

        super().__init__(
            **kwargs
        )

        self.set_title(
            "DebGear"
        )

        self.set_default_size(
            900,
            720
        )

        self.devices = []
        # switches keyed by device_id (see scan_hardware note),
        # value is (package_name, Gtk.Switch)
        self.switches = {}

        # NOTE (fix): every button that triggers a privileged
        # pkexec operation lives in this list so set_busy() can
        # disable ALL of them at once, not just "Apply Changes".
        # Previously Repair/Update stayed clickable while another
        # operation was already running, which could fire two
        # concurrent pkexec/apt-get calls against the same lock.
        self.action_buttons = []
        self.busy = False

        # Cached result of the single get_nvidia_status() call
        # for the current refresh cycle.
        self.nvidia_status = None

        self.setup_css()
        self.build_ui()
        self.start_refresh()

    # ========================================================
    # CSS
    # ========================================================

    def setup_css(self):

        provider = Gtk.CssProvider()

        provider.load_from_data(
            b"""
            window {
                background: #17171c;
            }

            .content {
                background: #17171c;
            }

            .page-title {
                font-size: 25px;
                font-weight: 700;
            }

            .page-description {
                color: alpha(white, 0.58);
                font-size: 13px;
            }

            .count-badge {
                background: alpha(white, 0.08);
                color: alpha(white, 0.62);
                border-radius: 999px;
                padding: 5px 10px;
                font-size: 11px;
            }

            .section-label {
                color: alpha(white, 0.48);
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }

            .device-card {
                background: #202027;
                border: 1px solid alpha(white, 0.055);
                border-radius: 13px;
                padding: 16px;
            }

            .device-card:hover {
                background: #24242c;
                border-color: alpha(white, 0.09);
            }

            .nvidia-card {
                background: #1d2922;
                border-color: alpha(#76b900, 0.28);
            }

            .device-name {
                font-size: 14px;
                font-weight: 650;
            }

            .driver-name {
                color: alpha(white, 0.60);
                font-size: 12px;
            }

            .kernel-text {
                color: alpha(white, 0.43);
                font-size: 11px;
            }

            .status-ok {
                background: alpha(#2ec27e, 0.13);
                color: #5be39b;
                border: 1px solid alpha(#2ec27e, 0.25);
                border-radius: 999px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 650;
            }

            .status-warning {
                background: alpha(#f9c74f, 0.13);
                color: #ffd866;
                border: 1px solid alpha(#f9c74f, 0.24);
                border-radius: 999px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 650;
            }

            .status-error {
                background: alpha(#ff453a, 0.13);
                color: #ff756e;
                border: 1px solid alpha(#ff453a, 0.24);
                border-radius: 999px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 650;
            }

            .status-update {
                background: alpha(#58a6ff, 0.13);
                color: #72b8ff;
                border: 1px solid alpha(#58a6ff, 0.25);
                border-radius: 999px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 650;
            }

            .version-label {
                color: alpha(white, 0.42);
                font-size: 11px;
            }

            .version-value {
                font-size: 12px;
                font-weight: 600;
            }

            .source-label {
                color: alpha(white, 0.38);
                font-size: 10px;
            }

            .repair-button {
                min-height: 32px;
                border-radius: 8px;
            }

            .update-button {
                min-height: 32px;
                border-radius: 8px;
            }

            .bottom-bar {
                background: #1d1d23;
                border-top: 1px solid alpha(white, 0.07);
                padding: 12px 20px;
            }

            .bottom-status {
                color: alpha(white, 0.68);
                font-size: 12px;
            }

            .apply-button {
                min-width: 190px;
                min-height: 40px;
                border-radius: 9px;
                font-weight: 650;
            }
            """
        )

        display = Gdk.Display.get_default()

        if display:

            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # ========================================================
    # BUILD UI
    # ========================================================

    def build_ui(self):

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL
        )

        # ----------------------------------------------------
        # HEADER
        # ----------------------------------------------------

        header = Adw.HeaderBar()

        title = Adw.WindowTitle(
            title="DebGear",
            subtitle="Hardware and driver management"
        )

        header.set_title_widget(
            title
        )

        root.append(
            header
        )

        # ----------------------------------------------------
        # SCROLL
        # ----------------------------------------------------

        scroll = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16
        )

        content.add_css_class(
            "content"
        )

        content.set_margin_top(26)
        content.set_margin_bottom(26)
        content.set_margin_start(30)
        content.set_margin_end(30)

        scroll.set_child(
            content
        )

        root.append(
            scroll
        )

        # ----------------------------------------------------
        # INTRO
        # ----------------------------------------------------

        intro = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12
        )

        intro_text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=3,
            hexpand=True
        )

        page_title = Gtk.Label(
            label="System Hardware",
            xalign=0
        )

        page_title.add_css_class(
            "page-title"
        )

        page_description = Gtk.Label(
            label=(
                "View detected graphics and network hardware "
                "and available drivers."
            ),
            xalign=0,
            wrap=True
        )

        page_description.add_css_class(
            "page-description"
        )

        intro_text.append(
            page_title
        )

        intro_text.append(
            page_description
        )

        intro.append(
            intro_text
        )

        self.device_count = Gtk.Label(
            label="0 devices"
        )

        self.device_count.add_css_class(
            "count-badge"
        )

        self.device_count.set_valign(
            Gtk.Align.CENTER
        )

        intro.append(
            self.device_count
        )

        content.append(
            intro
        )

        # ----------------------------------------------------
        # SECTION
        # ----------------------------------------------------

        section = Gtk.Label(
            label="HARDWARE",
            xalign=0
        )

        section.add_css_class(
            "section-label"
        )

        content.append(
            section
        )

        # ----------------------------------------------------
        # DEVICE LIST
        # ----------------------------------------------------

        self.device_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8
        )

        content.append(
            self.device_list
        )

        # ----------------------------------------------------
        # BOTTOM BAR
        # ----------------------------------------------------

        bottom = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=15
        )

        bottom.add_css_class(
            "bottom-bar"
        )

        self.bottom_status = Gtk.Label(
            label="Checking drivers...",
            xalign=0,
            hexpand=True
        )

        self.bottom_status.add_css_class(
            "bottom-status"
        )

        bottom.append(
            self.bottom_status
        )

        self.apply_button = Gtk.Button(
            label="Apply Changes"
        )

        self.apply_button.add_css_class(
            "suggested-action"
        )

        self.apply_button.add_css_class(
            "apply-button"
        )

        self.apply_button.connect(
            "clicked",
            self.on_apply
        )

        self.action_buttons.append(
            self.apply_button
        )

        bottom.append(
            self.apply_button
        )

        root.append(
            bottom
        )

        self.set_content(
            root
        )

    # ========================================================
    # REFRESH (fix: scanning now happens off the GTK thread)
    # ========================================================

    def start_refresh(self):

        self.set_busy(True)

        self.bottom_status.set_text(
            "Checking drivers..."
        )

        thread = threading.Thread(
            target=self._scan_in_background,
            daemon=True
        )

        thread.start()

    def _scan_in_background(self):

        # Runs off the main thread: scan_hardware() and
        # get_nvidia_status() together issue a couple dozen
        # subprocess calls (lspci, dpkg-query, apt-cache policy,
        # apt-cache madison, lsmod, nvidia-smi). Doing this on the
        # GTK thread is what caused the UI to freeze on startup and
        # after every apply/update/repair.
        devices = scan_hardware()

        nvidia_status = None

        if any(
            d["vendor"] == "10de" and d["type"] == "gpu"
            for d in devices
        ):

            nvidia_status = get_nvidia_status()

        GLib.idle_add(
            self._apply_refresh,
            devices,
            nvidia_status
        )

    def _apply_refresh(self, devices, nvidia_status):

        self.devices = devices
        self.nvidia_status = nvidia_status
        self.switches.clear()

        self.device_count.set_text(
            f"{len(self.devices)} devices"
        )

        while True:

            child = (
                self.device_list
                .get_first_child()
            )

            if child is None:
                break

            self.device_list.remove(
                child
            )

        for device in self.devices:

            card = self.create_device_card(
                device
            )

            self.device_list.append(
                card
            )

        self.update_global_status()

        self.set_busy(False)

        return False

    # ========================================================
    # DEVICE CARD
    # ========================================================

    def create_device_card(
        self,
        device
    ):

        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10
        )

        card.add_css_class(
            "device-card"
        )

        is_nvidia = (
            device["vendor"] == "10de"
            and device["type"] == "gpu"
        )

        if is_nvidia:

            card.add_css_class(
                "nvidia-card"
            )

        # ----------------------------------------------------
        # TOP GRID
        # ----------------------------------------------------

        grid = Gtk.Grid(
            column_spacing=22,
            row_spacing=5
        )

        grid.set_hexpand(
            True
        )

        # ----------------------------------------------------
        # LEFT
        # ----------------------------------------------------

        left = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=3
        )

        left.set_hexpand(
            True
        )

        name = Gtk.Label(
            label=device["name"],
            xalign=0,
            wrap=True
        )

        name.add_css_class(
            "device-name"
        )

        driver = Gtk.Label(
            label=device["driver_name"],
            xalign=0
        )

        driver.add_css_class(
            "driver-name"
        )

        kernel = Gtk.Label(
            label=f"Kernel driver: {device['kernel']}",
            xalign=0
        )

        kernel.add_css_class(
            "kernel-text"
        )

        left.append(
            name
        )

        left.append(
            driver
        )

        left.append(
            kernel
        )

        grid.attach(
            left,
            0,
            0,
            1,
            1
        )

        # ----------------------------------------------------
        # RIGHT
        # ----------------------------------------------------

        right = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=7
        )

        right.set_halign(
            Gtk.Align.END
        )

        # ----------------------------------------------------
        # STATUS (uses cached self.nvidia_status, no re-query)
        # ----------------------------------------------------

        status = self.create_status_widget(
            device,
            is_nvidia
        )

        right.append(
            status
        )

        # ----------------------------------------------------
        # VERSION GRID
        # ----------------------------------------------------

        version_grid = Gtk.Grid(
            column_spacing=8,
            row_spacing=2
        )

        current_label = Gtk.Label(
            label="Installed:"
        )

        current_label.add_css_class(
            "version-label"
        )

        current_value = Gtk.Label(
            label=device[
                "installed_version"
            ]
        )

        current_value.add_css_class(
            "version-value"
        )

        candidate_label = Gtk.Label(
            label="APT candidate:"
        )

        candidate_label.add_css_class(
            "version-label"
        )

        candidate_value = Gtk.Label(
            label=device[
                "candidate_version"
            ]
        )

        candidate_value.add_css_class(
            "version-value"
        )

        newest_label = Gtk.Label(
            label="Latest:"
        )

        newest_label.add_css_class(
            "version-label"
        )

        newest_value = Gtk.Label(
            label=device[
                "newest_version"
            ]
        )

        newest_value.add_css_class(
            "version-value"
        )

        version_grid.attach(
            current_label,
            0,
            0,
            1,
            1
        )

        version_grid.attach(
            current_value,
            1,
            0,
            1,
            1
        )

        version_grid.attach(
            candidate_label,
            0,
            1,
            1,
            1
        )

        version_grid.attach(
            candidate_value,
            1,
            1,
            1,
            1
        )

        version_grid.attach(
            newest_label,
            0,
            2,
            1,
            1
        )

        version_grid.attach(
            newest_value,
            1,
            2,
            1,
            1
        )

        right.append(
            version_grid
        )

        # ----------------------------------------------------
        # SOURCE
        # ----------------------------------------------------

        source = Gtk.Label(
            label=device["source"],
            xalign=1
        )

        source.add_css_class(
            "source-label"
        )

        right.append(
            source
        )

        grid.attach(
            right,
            1,
            0,
            1,
            1
        )

        card.append(
            grid
        )

        # ----------------------------------------------------
        # NVIDIA ACTIONS (uses cached self.nvidia_status)
        # ----------------------------------------------------

        if is_nvidia and self.nvidia_status:

            nvidia = self.nvidia_status

            # -----------------------------------------------
            # UPDATE AVAILABLE
            # -----------------------------------------------

            if nvidia["has_update"]:

                separator = Gtk.Separator(
                    orientation=Gtk.Orientation.HORIZONTAL
                )

                card.append(
                    separator
                )

                update_row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=12
                )

                update_text = Gtk.Label(
                    label=(
                        f"New NVIDIA driver available: "
                        f"{nvidia['newest_version']}"
                    ),
                    xalign=0,
                    hexpand=True
                )

                update_text.add_css_class(
                    "driver-name"
                )

                update_row.append(
                    update_text
                )

                update_button = Gtk.Button(
                    label="Update NVIDIA"
                )

                update_button.add_css_class(
                    "update-button"
                )

                update_button.connect(
                    "clicked",
                    self.on_nvidia_update
                )

                # fix: tracked so set_busy() disables it too
                self.action_buttons.append(
                    update_button
                )

                update_button.set_sensitive(
                    not self.busy
                )

                update_row.append(
                    update_button
                )

                card.append(
                    update_row
                )

            # -----------------------------------------------
            # REPAIR
            # -----------------------------------------------

            if nvidia["status"] not in (
                "active",
            ):

                separator = Gtk.Separator(
                    orientation=Gtk.Orientation.HORIZONTAL
                )

                card.append(
                    separator
                )

                action_row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=12
                )

                description = Gtk.Label(
                    label=(
                        "A problem with the NVIDIA "
                        "kernel module was detected."
                    ),
                    xalign=0,
                    hexpand=True
                )

                description.add_css_class(
                    "driver-name"
                )

                action_row.append(
                    description
                )

                repair_button = Gtk.Button(
                    label="Repair NVIDIA Driver"
                )

                repair_button.add_css_class(
                    "repair-button"
                )

                repair_button.connect(
                    "clicked",
                    self.on_nvidia_repair
                )

                # fix: tracked so set_busy() disables it too
                self.action_buttons.append(
                    repair_button
                )

                repair_button.set_sensitive(
                    not self.busy
                )

                action_row.append(
                    repair_button
                )

                card.append(
                    action_row
                )

        # ----------------------------------------------------
        # PACKAGE SWITCH
        # ----------------------------------------------------

        if device["pkg"]:

            package_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10
            )

            package_label = Gtk.Label(
                label="Package enabled",
                xalign=0,
                hexpand=True
            )

            package_label.add_css_class(
                "driver-name"
            )

            package_switch = Gtk.Switch(
                active=device["installed"]
            )

            package_switch.set_valign(
                Gtk.Align.CENTER
            )

            # fix: keyed by device_id, not package name, so two
            # devices sharing the same package (e.g. dual NVIDIA
            # GPUs) each keep their own switch instead of the
            # second one overwriting the first in the dict.
            self.switches[
                device["device_id"]
            ] = (device["pkg"], package_switch)

            package_row.append(
                package_label
            )

            package_row.append(
                package_switch
            )

            card.append(
                package_row
            )

        return card

    # ========================================================
    # STATUS WIDGET (uses cached self.nvidia_status)
    # ========================================================

    def create_status_widget(
        self,
        device,
        is_nvidia
    ):

        if is_nvidia and self.nvidia_status:

            nvidia = self.nvidia_status

            status = nvidia[
                "status"
            ]

            if status == "active":

                label = "✓ Driver active"
                css = "status-ok"

            elif status == "kernel-active":

                label = "⚠ Kernel driver active"
                css = "status-warning"

            elif status == "nouveau":

                label = "⚠ Nouveau active"
                css = "status-warning"

            elif status == "module-loaded":

                label = "⚠ NVIDIA module loaded"
                css = "status-warning"

            elif status == "installed-not-active":

                label = "⚠ Driver not active"
                css = "status-warning"

            else:

                label = "⚠ Driver not installed"
                css = "status-error"

        elif device["pkg"]:

            if device["installed"]:

                label = "✓ Package installed"
                css = "status-ok"

            else:

                label = "⚠ Package not installed"
                css = "status-warning"

        else:

            label = "✓ System driver"
            css = "status-ok"

        status_label = Gtk.Label(
            label=label
        )

        status_label.add_css_class(
            css
        )

        status_label.set_halign(
            Gtk.Align.END
        )

        return status_label

    # ========================================================
    # GLOBAL STATUS (uses cached self.nvidia_status)
    # ========================================================

    def update_global_status(self):

        if not self.nvidia_status:

            self.bottom_status.set_text(
                "System drivers are working properly."
            )

            return

        nvidia = self.nvidia_status

        if nvidia["status"] == "active":

            if nvidia["has_update"]:

                self.bottom_status.set_text(
                    "NVIDIA is active. A new driver version is available."
                )

            else:

                self.bottom_status.set_text(
                    "NVIDIA driver is active and up to date."
                )

        elif nvidia["status"] == "kernel-active":

            self.bottom_status.set_text(
                "NVIDIA kernel driver is active, but NVIDIA-SMI is not working."
            )

        elif nvidia["status"] == "nouveau":

            self.bottom_status.set_text(
                "Nouveau is active; the NVIDIA driver is not being used."
            )

        elif nvidia["status"] == "module-loaded":

            self.bottom_status.set_text(
                "NVIDIA kernel module is loaded, but the driver connection should be checked."
            )

        elif nvidia["status"] == "installed-not-active":

            self.bottom_status.set_text(
                "NVIDIA package is installed, but the kernel driver is not active."
            )

        else:

            self.bottom_status.set_text(
                "NVIDIA driver is not installed."
            )

    # ========================================================
    # APPLY
    # ========================================================

    def on_apply(
        self,
        button
    ):

        # fix: aggregate by package name, since several devices
        # (device_id keys) can point at the same package.
        wanted_state = {}

        for device_id, (package, switch) in self.switches.items():

            wanted_state[package] = switch.get_active()

        changes = []

        for package, active in wanted_state.items():

            installed, _ = (
                get_installed_package_version(
                    package
                )
            )

            if (
                active
                and not installed
            ):

                changes.append(
                    (
                        "install",
                        package
                    )
                )

            elif (
                not active
                and installed
            ):

                changes.append(
                    (
                        "remove",
                        package
                    )
                )

        if not changes:

            self.show_dialog(
                "No Changes",
                "There are no package changes to apply."
            )

            return

        self.set_busy(
            True
        )

        self.bottom_status.set_text(
            "Applying package changes..."
        )

        thread = threading.Thread(
            target=self.execute_package_changes,
            args=(changes,),
            daemon=True
        )

        thread.start()

    # ========================================================
    # PACKAGE CHANGES
    # ========================================================

    def execute_package_changes(
        self,
        changes
    ):

        logs = []
        success = True

        for action, package in changes:

            if action == "install":

                command = [
                    "pkexec",
                    "apt-get",
                    "install",
                    "-y",
                    package
                ]

            else:

                command = [
                    "pkexec",
                    "apt-get",
                    "remove",
                    "-y",
                    package
                ]

            code, output = run_command(
                command
            )

            logs.append(
                output
            )

            if code != 0:

                success = False
                break

        GLib.idle_add(
            self.operation_finished,
            success,
            "\n\n".join(logs)
        )

    # ========================================================
    # NVIDIA UPDATE
    # ========================================================

    def on_nvidia_update(
        self,
        button
    ):

        if not self.nvidia_status:
            return

        nvidia = self.nvidia_status

        newest = nvidia[
            "newest_version"
        ]

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Update NVIDIA Driver?",
            body=(
                f"Installed version:\n"
                f"{nvidia['installed_version']}\n\n"
                f"New version:\n"
                f"{newest}\n\n"
                f"APT will attempt to install this version "
                f"from the configured repositories."
            )
        )

        dialog.add_response(
            "cancel",
            "Cancel"
        )

        dialog.add_response(
            "update",
            "Update"
        )

        dialog.set_default_response(
            "update"
        )

        dialog.connect(
            "response",
            self.nvidia_update_response,
            newest
        )

        dialog.present()

    # ========================================================
    # NVIDIA UPDATE RESPONSE
    # ========================================================

    def nvidia_update_response(
        self,
        dialog,
        response,
        version
    ):

        if response != "update":
            return

        self.set_busy(
            True
        )

        self.bottom_status.set_text(
            "Updating NVIDIA driver..."
        )

        thread = threading.Thread(
            target=self.execute_nvidia_update,
            args=(version,),
            daemon=True
        )

        thread.start()

    # ========================================================
    # NVIDIA UPDATE
    # ========================================================

    def execute_nvidia_update(
        self,
        version
    ):

        command = [
            "pkexec",
            "apt-get",
            "install",
            "-y",
            f"nvidia-driver={version}"
        ]

        code, output = run_command(
            command
        )

        GLib.idle_add(
            self.operation_finished,
            code == 0,
            output
        )

    # ========================================================
    # NVIDIA REPAIR
    # ========================================================

    def on_nvidia_repair(
        self,
        button
    ):

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Repair NVIDIA Driver?",
            body=(
                "APT will be updated, DKMS and kernel headers "
                "will be checked. The NVIDIA kernel module will "
                "be rebuilt and an attempt will be made to load it."
            )
        )

        dialog.add_response(
            "cancel",
            "Cancel"
        )

        dialog.add_response(
            "repair",
            "Repair"
        )

        dialog.set_default_response(
            "repair"
        )

        dialog.connect(
            "response",
            self.nvidia_repair_response
        )

        dialog.present()

    # ========================================================
    # NVIDIA REPAIR RESPONSE
    # ========================================================

    def nvidia_repair_response(
        self,
        dialog,
        response
    ):

        if response != "repair":
            return

        self.set_busy(
            True
        )

        self.bottom_status.set_text(
            "Repairing NVIDIA driver..."
        )

        thread = threading.Thread(
            target=self.execute_nvidia_repair,
            daemon=True
        )

        thread.start()

    # ========================================================
    # NVIDIA REPAIR
    # ========================================================

    def execute_nvidia_repair(self):

        repair_script = r"""
set -e

echo "========================================"
echo " NVIDIA DRIVER REPAIR"
echo "========================================"

echo
echo "=== APT UPDATE ==="
apt-get update

echo
echo "=== DKMS / KERNEL HEADERS ==="
apt-get install -y dkms linux-headers-$(uname -r)

echo
echo "=== DKMS AUTOINSTALL ==="
dkms autoinstall

echo
echo "=== DEPMOD ==="
depmod -a

echo
echo "=== NVIDIA MODULE ==="
modprobe nvidia

echo
echo "=== NVIDIA-SMI ==="
nvidia-smi

echo
echo "========================================"
echo " NVIDIA DRIVER SUCCESSFULLY ACTIVATED"
echo "========================================"
"""

        command = [
            "pkexec",
            "bash",
            "-c",
            repair_script
        ]

        code, output = run_command(
            command
        )

        GLib.idle_add(
            self.operation_finished,
            code == 0,
            output
        )

    # ========================================================
    # BUSY (fix: now disables every tracked action button, not
    # just "Apply Changes" — prevents overlapping pkexec calls)
    # ========================================================

    def set_busy(
        self,
        busy
    ):

        self.busy = busy

        for button in self.action_buttons:

            button.set_sensitive(
                not busy
            )

    # ========================================================
    # OPERATION FINISHED
    # ========================================================

    def operation_finished(
        self,
        success,
        output
    ):

        if success:

            self.bottom_status.set_text(
                "Operation completed successfully."
            )

            # start_refresh() calls set_busy(True) itself and the
            # background scan will call set_busy(False) when done,
            # so we don't clear busy here first.
            self.start_refresh()

            self.show_dialog(
                "Operation Complete",
                "The driver operation completed successfully."
            )

        else:

            self.set_busy(
                False
            )

            self.bottom_status.set_text(
                "An error occurred during the operation."
            )

            if len(output) > 6000:

                output = output[-6000:]

            self.show_dialog(
                "Operation Failed",
                "Command output:\n\n" + output
            )

        return False

    # ========================================================
    # DIALOG
    # ========================================================

    def show_dialog(
        self,
        heading,
        body
    ):

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=body
        )

        dialog.add_response(
            "ok",
            "OK"
        )

        dialog.set_default_response(
            "ok"
        )

        dialog.present()


# ============================================================
# APPLICATION
# ============================================================

class DriverManager(
    Adw.Application
):

    def __init__(self):

        super().__init__(
            application_id="com.mozcelik.DebGear",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

        self.connect(
            "activate",
            self.on_activate
        )

    def on_activate(
        self,
        app
    ):

        window = (
            self.props.active_window
        )

        if window is None:

            window = DriverWindow(
                application=app
            )

        window.present()


# ============================================================
# MAIN
# ============================================================

def main():

    app = DriverManager()

    return app.run(
        sys.argv
    )


if __name__ == "__main__":

    sys.exit(
        main()
    )
