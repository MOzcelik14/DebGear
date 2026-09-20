#!/usr/bin/env python3

import gi
import subprocess
import sys
import re
import threading
import os
from debgear_diagnostics import (
    operating_system, supports_driver_changes, detect_nvidia_package,
    query_gpu, firmware_report, firmware_messages, kernel_report,
    record_operation, read_history,
)
import shutil

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

def run_command(command, env_extra=None, timeout=30):
    """Execute an argv list without a shell; probes must not hang the UI forever."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, f"Command timed out: {command[0]}"
    except (OSError, ValueError) as exc:
        return 1, str(exc)


def command_output(command, env_extra=None):
    code, output = run_command(command, env_extra)
    return output if code == 0 else ""

# ============================================================
# VERSION COMPARISON
# ============================================================

def version_compare(version_a, version_b):
    """Compare Debian versions using dpkg; None means comparison unavailable."""
    if not version_a or not version_b:
        return None
    if version_a == version_b:
        return 0
    for relation, result in (("gt", 1), ("lt", -1), ("eq", 0)):
        code, _ = run_command(
            ["dpkg", "--compare-versions", version_a, relation, version_b]
        )
        if code == 0:
            return result
        if code != 1:
            return None
    return None


def version_is_newer(candidate, installed):
    return version_compare(candidate, installed) == 1

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
        ) == 1:

            newest = version

    return newest


# ============================================================
# DETECT VERSION SOURCE
# ============================================================

def get_version_source(package, version):
    """Return a descriptive source, without assuming the Debian codename."""
    if not version or version == "Unknown":
        return "Unknown"
    output = command_output(
        ["apt-cache", "madison", package], {"LC_ALL": "C", "LANG": "C"}
    )
    sources = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 3 and parts[1] == version:
            sources.append(parts[2].lower())
    if not sources:
        return "Installed / local" if get_installed_package_version(package)[1] == version else "Unknown"
    if any("backports" in source for source in sources):
        return "Backports"
    if any("unstable" in source or "/sid" in source for source in sources):
        return "Debian Unstable"
    if any("testing" in source for source in sources):
        return "Debian Testing"
    return "APT repository"

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

def get_nvidia_status(kernel_driver=None):
    package_name = detect_nvidia_package() or "nvidia-driver"
    package = get_pkg_info(package_name)
    if kernel_driver is None:
        kernel_driver = get_nvidia_kernel_driver()
    module_loaded = is_nvidia_module_loaded()
    nvidia_smi, smi_output = check_nvidia_smi()
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

    # The highest version in any repository is NOT necessarily installable
    # by a normal apt-get install. Only the APT candidate drives updates.
    candidate = package["candidate_version"]
    installed = package["installed_version"]
    has_update = (
        package["installed"]
        and candidate != "Unknown"
        and installed != "Unknown"
        and version_is_newer(candidate, installed)
    )
    secure_boot = command_output(["mokutil", "--sb-state"]) if shutil.which("mokutil") else ""
    release = os.uname().release
    headers_installed, _ = get_installed_package_version(f"linux-headers-{release}")
    dkms_output = command_output(["dkms", "status", "-k", release]) if shutil.which("dkms") else ""

    return {
        "installed": package["installed"],
        "package_name": package_name,
        "installed_version": installed,
        "candidate_version": candidate,
        "newest_version": package["newest_version"],
        "source": package["source"],
        "kernel_driver": kernel_driver,
        "module_loaded": module_loaded,
        "nvidia_smi": nvidia_smi,
        "status": status,
        "has_update": has_update,
        "smi_output": smi_output,
        "secure_boot": secure_boot,
        "kernel_release": release,
        "headers_installed": headers_installed,
        "dkms_status": dkms_output,
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
    detected_nvidia_package = detect_nvidia_package() or "nvidia-driver"

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
                "kernel": None,
                "modules": []
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
            modules = re.search(r"Kernel modules:\s*(.+)", line)
            if modules:
                current["modules"] = [
                    item.strip() for item in modules.group(1).split(",")
                ]

    # ========================================================
    # RESULT
    # ========================================================

    result = []

    package_cache = {}

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

        package = (detected_nvidia_package
                   if vendor == "10de" and device["type"] == "gpu"
                   else info["pkg"])
        if package not in package_cache:
            package_cache[package] = get_pkg_info(package)
        package_info = package_cache[package]

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
            "modules": device["modules"],
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

        self.set_default_size(960, 740)
        self.set_size_request(640, 520)

        self.devices = []
        # switches keyed by device_id (see scan_hardware note),
        # value is (package_name, Gtk.Switch)
        self.switches = {}
        self.original_state = {}
        self.device_card_map = []

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
        self.os_info = operating_system()
        self.supported_host = supports_driver_changes(self.os_info)
        self.details = {}
        self.current_action = "Driver operation"

        self.setup_css()
        self.build_ui()
        self.start_refresh()

    # ========================================================
    # CSS
    # ========================================================

    def setup_css(self):
        """Adaptive libadwaita styling: respect the system's light/dark preference."""
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
            .driver-sidebar {
                background: alpha(@window_fg_color, 0.025);
                padding: 14px 10px;
            }
            .sidebar-brand { padding: 14px 10px 22px; }
            .brand-title { font-size: 12px; font-weight: 800; }
            .brand-icon {
                color: #92cf49;
                background: alpha(#76b900, 0.16);
                padding: 9px;
                border-radius: 12px;
            }
            .host-badge {
                color: alpha(@window_fg_color, 0.68);
                padding: 12px;
                border-radius: 11px;
                background: alpha(@window_fg_color, 0.06);
                font-size: 11px;
            }
            .nav-item {
                padding: 10px;
                border-radius: 10px;
                background: transparent;
                box-shadow: none;
                font-weight: 600;
            }
            .nav-item:hover { background: alpha(@window_fg_color, 0.06); }
            .nav-active {
                background: alpha(@accent_bg_color, 0.18);
                color: @accent_color;
            }
            .debgear-content { padding-bottom: 18px; }
            .driver-hero {
                padding: 25px;
                border-radius: 18px;
                background: linear-gradient(115deg,
                    alpha(#76b900, 0.19), alpha(@accent_bg_color, 0.065));
                border: 1px solid alpha(#76b900, 0.35);
            }
            .eyebrow {
                color: @accent_color;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            .page-title { font-size: 30px; font-weight: 800; }
            .hero-description, .subdued {
                color: alpha(@window_fg_color, 0.70);
                font-size: 12px;
            }
            .section-heading { font-size: 19px; font-weight: 750; }
            .metric-card {
                padding: 17px;
                border-radius: 14px;
                background: @card_bg_color;
                border: 1px solid alpha(@window_fg_color, 0.09);
            }
            .metric-value { font-size: 28px; font-weight: 800; }
            .metric-icon { color: @accent_color; }
            .notice-text {
                padding: 13px 15px;
                border-radius: 11px;
                background: alpha(@accent_bg_color, 0.08);
                color: @window_fg_color;
                font-size: 12px;
            }
            .count-badge {
                border-radius: 99px;
                padding: 6px 12px;
                background: alpha(@window_fg_color, 0.08);
            }
            .device-card {
                padding: 19px;
                border-radius: 15px;
                background: @card_bg_color;
                border: 1px solid alpha(@window_fg_color, 0.10);
            }
            .nvidia-card { border-color: alpha(#76b900, 0.49); }
            .device-icon {
                padding: 12px;
                border-radius: 12px;
                background: alpha(@window_fg_color, 0.06);
            }
            .device-name { font-size: 15px; font-weight: bold; }
            .driver-name, .kernel-text, .version-label {
                color: alpha(@window_fg_color, 0.70);
                font-size: 12px;
            }
            .version-value { font-size: 12px; font-weight: 650; }
            .status-ok, .status-warning, .status-error {
                border-radius: 99px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            .status-ok { color: #237b48; background: alpha(#2ec27e, 0.18); }
            .status-warning { color: #986309; background: alpha(#f4b400, 0.19); }
            .status-error { color: #ae3131; background: alpha(#ed333b, 0.15); }
            .bottom-bar {
                background: @window_bg_color;
                padding: 12px 20px;
                border-top: 1px solid alpha(@window_fg_color, 0.09);
            }
            .bottom-status {
                color: alpha(@window_fg_color, 0.73);
                font-size: 12px;
            }
            .apply-button { min-width: 170px; min-height: 40px; }
            .action-row { padding-top: 9px; }
        """)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # ========================================================
    # BUILD UI
    # ========================================================

    def build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title="DebGear", subtitle="Driver Center · 0.3"
        ))
        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Rescan drivers and firmware")
        self.refresh_button.connect("clicked", lambda *_: self.start_refresh())
        header.pack_end(self.refresh_button)
        self.action_buttons.append(self.refresh_button)
        root.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)
        root.append(body)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar.set_size_request(198, -1)
        sidebar.add_css_class("driver-sidebar")
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        brand.add_css_class("sidebar-brand")
        icon = Gtk.Image.new_from_icon_name("applications-system-symbolic")
        icon.set_pixel_size(28)
        icon.add_css_class("brand-icon")
        brand.append(icon)
        brand_words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand_title = Gtk.Label(label="DRIVER CENTER", xalign=0)
        brand_title.add_css_class("brand-title")
        brand_words.append(brand_title)
        brand_sub = Gtk.Label(label="Debian hardware tools", xalign=0)
        brand_sub.add_css_class("subdued")
        brand_words.append(brand_sub)
        brand.append(brand_words)
        sidebar.append(brand)

        self.nav_buttons = {}
        for section, icon_name, title in (
            ("overview", "view-dashboard-symbolic", "Overview"),
            ("graphics", "video-display-symbolic", "Graphics & GPU"),
            ("network", "network-wireless-symbolic", "Network"),
            ("firmware", "drive-harddisk-symbolic", "Firmware"),
            ("kernel", "utilities-system-monitor-symbolic", "Kernel & DKMS"),
            ("activity", "document-open-recent-symbolic", "Activity"),
        ):
            nav = Gtk.Button()
            nav.add_css_class("nav-item")
            nav_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            nav_icon = Gtk.Image.new_from_icon_name(icon_name)
            nav_icon.set_pixel_size(18)
            nav_row.append(nav_icon)
            label = Gtk.Label(label=title, xalign=0, hexpand=True)
            nav_row.append(label)
            nav.set_child(nav_row)
            nav.connect("clicked", self.select_page, section)
            sidebar.append(nav)
            self.nav_buttons[section] = nav
        spacer = Gtk.Box(vexpand=True)
        sidebar.append(spacer)
        self.host_badge = Gtk.Label(
            label=self.os_info.get("PRETTY_NAME", "Linux"), xalign=0, wrap=True
        )
        self.host_badge.add_css_class("host-badge")
        sidebar.append(self.host_badge)
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(170)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        body.append(self.stack)

        overview = self.make_page()
        self.stack.add_named(overview, "overview")
        overview_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        overview_hero.add_css_class("driver-hero")
        eyebrow = Gtk.Label(label="SYSTEM OVERVIEW", xalign=0)
        eyebrow.add_css_class("eyebrow")
        overview_hero.append(eyebrow)
        hero_title = Gtk.Label(label="Your drivers. In control.", xalign=0, wrap=True)
        hero_title.add_css_class("page-title")
        overview_hero.append(hero_title)
        self.hero_description = Gtk.Label(
            label="Inspecting your devices and driver health…",
            xalign=0, wrap=True,
        )
        self.hero_description.add_css_class("hero-description")
        overview_hero.append(self.hero_description)
        self.page_content["overview"].append(overview_hero)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.metrics_labels = {}
        for key, title, icon_name in (
            ("devices", "Devices", "computer-symbolic"),
            ("drivers", "Kernel drivers", "emblem-ok-symbolic"),
            ("attention", "Needs review", "dialog-warning-symbolic"),
        ):
            metric = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            metric.add_css_class("metric-card")
            metric.set_hexpand(True)
            metric_icon = Gtk.Image.new_from_icon_name(icon_name)
            metric_icon.set_halign(Gtk.Align.START)
            metric_icon.add_css_class("metric-icon")
            metric.append(metric_icon)
            value = Gtk.Label(label="—", xalign=0)
            value.add_css_class("metric-value")
            metric.append(value)
            caption = Gtk.Label(label=title, xalign=0, wrap=True)
            caption.add_css_class("subdued")
            metric.append(caption)
            metrics.append(metric)
            self.metrics_labels[key] = value
        self.page_content["overview"].append(metrics)

        self.summary_label = Gtk.Label(
            label="Scanning your system…", xalign=0, wrap=True
        )
        self.summary_label.add_css_class("notice-text")
        self.page_content["overview"].append(self.summary_label)
        self.device_count = Gtk.Label(label="0 devices")
        self.device_count.add_css_class("count-badge")
        self.page_content["overview"].append(self.make_section(
            "Detected hardware", "Device and driver status"
        ))
        self.overview_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self.page_content["overview"].append(self.overview_list)

        graphics = self.make_page()
        self.stack.add_named(graphics, "graphics")
        self.page_content["graphics"].append(self.make_section(
            "Graphics & GPU", "Manage installed graphics packages and inspect GPU health"
        ))
        self.gpu_metrics_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.page_content["graphics"].append(self.gpu_metrics_box)
        self.graphics_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.page_content["graphics"].append(self.graphics_list)

        network = self.make_page()
        self.stack.add_named(network, "network")
        self.page_content["network"].append(self.make_section(
            "Network & Wireless", "Adapters, available kernel modules and active bindings"
        ))
        self.network_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.page_content["network"].append(self.network_list)

        firmware = self.make_page()
        self.stack.add_named(firmware, "firmware")
        self.page_content["firmware"].append(self.make_section(
            "Firmware", "Read-only fwupd inventory and recent kernel firmware messages"
        ))
        self.firmware_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self.page_content["firmware"].append(self.firmware_list)
        self.page_content["firmware"].append(self.make_section(
            "Kernel firmware messages", "Recent messages are not a complete system audit"
        ))
        self.firmware_messages_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self.page_content["firmware"].append(self.firmware_messages_box)

        kernel_page = self.make_page()
        self.stack.add_named(kernel_page, "kernel")
        self.page_content["kernel"].append(self.make_section(
            "Kernel & DKMS", "Compatibility signals for the currently running kernel"
        ))
        self.kernel_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10
        )
        self.page_content["kernel"].append(self.kernel_list)

        activity = self.make_page()
        self.stack.add_named(activity, "activity")
        self.page_content["activity"].append(self.make_section(
            "Activity & diagnostics", "Local driver action history without privileged logs"
        ))
        note = Gtk.Label(
            label="History stores only action names, timestamps and results.",
            xalign=0, wrap=True,
        )
        note.add_css_class("subdued")
        self.page_content["activity"].append(note)
        copy_button = Gtk.Button(label="Copy diagnostic summary")
        copy_button.set_halign(Gtk.Align.START)
        copy_button.connect("clicked", self.copy_diagnostics)
        self.page_content["activity"].append(copy_button)
        self.history_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self.page_content["activity"].append(self.history_list)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom.add_css_class("bottom-bar")
        self.bottom_status = Gtk.Label(
            label="Checking drivers…", xalign=0, hexpand=True, wrap=True
        )
        self.bottom_status.add_css_class("bottom-status")
        bottom.append(self.bottom_status)
        self.apply_button = Gtk.Button(label="No pending changes")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.add_css_class("apply-button")
        self.apply_button.connect("clicked", self.on_apply)
        self.apply_button.set_sensitive(False)
        self.action_buttons.append(self.apply_button)
        bottom.append(self.apply_button)
        root.append(bottom)
        self.set_content(root)
        self.select_page(None, "overview")

    def make_page(self):
        if not hasattr(self, "page_content"):
            self.page_content = {}
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        clamp = Adw.Clamp()
        clamp.set_maximum_size(1020)
        clamp.set_tightening_threshold(680)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=17)
        content.set_margin_top(28)
        content.set_margin_bottom(36)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.add_css_class("debgear-content")
        clamp.set_child(content)
        scroll.set_child(clamp)
        keys = ("overview", "graphics", "network", "firmware", "kernel", "activity")
        self.page_content[keys[len(self.page_content)]] = content
        return scroll

    def make_section(self, title, subtitle):
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        name = Gtk.Label(label=title, xalign=0, wrap=True)
        name.add_css_class("section-heading")
        group.append(name)
        secondary = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        secondary.add_css_class("subdued")
        group.append(secondary)
        return group

    def select_page(self, _button, section):
        self.stack.set_visible_child_name(section)
        for name, button in self.nav_buttons.items():
            if name == section:
                button.add_css_class("nav-active")
            else:
                button.remove_css_class("nav-active")
        self.apply_button.set_visible(section == "graphics")

    def _clear(self, box):
        while box.get_first_child():
            box.remove(box.get_first_child())

    def _notice(self, text, css="notice-text"):
        label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        label.add_css_class(css)
        return label

    def _info_card(self, title, lines, accent=False):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        card.add_css_class("device-card")
        if accent:
            card.add_css_class("nvidia-card")
        title_label = Gtk.Label(label=title, xalign=0, wrap=True)
        title_label.add_css_class("device-name")
        card.append(title_label)
        for caption, value in lines:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            label = Gtk.Label(label=caption, xalign=0, hexpand=True, wrap=True)
            label.add_css_class("subdued")
            row.append(label)
            detail = Gtk.Label(label=str(value), xalign=1, wrap=True, selectable=True)
            detail.add_css_class("version-value")
            row.append(detail)
            card.append(row)
        return card

    def copy_diagnostics(self, _button):
        lines = [
            "DebGear driver diagnostics",
            "Operating system: " + self.os_info.get("PRETTY_NAME", "Unknown"),
            "Read-only host: " + ("no" if self.supported_host else "yes"),
        ]
        for device in self.devices:
            lines.append(
                device["name"] + " | bound: " + device["kernel"]
                + " | available: " + ", ".join(device.get("modules", []))
            )
        if self.nvidia_status:
            lines.extend([
                "NVIDIA package: " + self.nvidia_status["package_name"],
                "NVIDIA state: " + self.nvidia_status["status"],
                "Installed: " + self.nvidia_status["installed_version"],
                "APT candidate: " + self.nvidia_status["candidate_version"],
            ])
        info = self.details.get("kernel", {})
        lines.extend([
            "Kernel: " + info.get("release", "Unknown"),
            "Matching headers: " + str(info.get("headers", "Unknown")),
        ])
        display = Gdk.Display.get_default()
        if display:
            display.get_clipboard().set("\n".join(lines))
            self.bottom_status.set_text("Diagnostic summary copied to clipboard.")


    # ========================================================
    # REFRESH (fix: scanning now happens off the GTK thread)
    # ========================================================

    def start_refresh(self, force=False):
        if self.busy and not force:
            return
        self.set_busy(True)
        self.bottom_status.set_text("Scanning devices, drivers and firmware…")
        threading.Thread(target=self._scan_in_background, daemon=True).start()

    def _scan_in_background(self):
        try:
            if not shutil.which("lspci"):
                raise RuntimeError("Install pciutils to scan PCI hardware (lspci).")
            devices = scan_hardware()
            nvidia_device = next(
                (d for d in devices
                 if d["vendor"] == "10de" and d["type"] == "gpu"),
                None,
            )
            nvidia_status = (
                get_nvidia_status(nvidia_device["kernel"])
                if nvidia_device else None
            )
            details = {
                "gpu": query_gpu() if nvidia_device else {"available": False,
                          "reason": "No NVIDIA GPU was detected"},
                "firmware": firmware_report(),
                "messages": firmware_messages(),
                "kernel": kernel_report(),
            }
            GLib.idle_add(self._apply_refresh, devices, nvidia_status, details)
        except Exception as exc:
            GLib.idle_add(self._refresh_failed, str(exc))

    def _refresh_failed(self, message):
        self.bottom_status.set_text("Unable to scan hardware.")
        self.summary_label.set_text("Hardware scan failed")
        self.set_busy(False)
        self.show_dialog("Hardware Scan Failed", message)
        return False

    def _apply_refresh(self, devices, nvidia_status, details):
        self.devices = devices
        self.nvidia_status = nvidia_status
        self.details = details
        self.switches.clear()
        self.original_state.clear()
        self.device_card_map.clear()
        self._nvidia_actions_added = False
        self.action_buttons = [self.refresh_button, self.apply_button]
        self.device_count.set_text(f"{len(devices)} devices")
        for box in (
            self.overview_list, self.graphics_list, self.network_list,
            self.gpu_metrics_box, self.firmware_list,
            self.firmware_messages_box, self.kernel_list, self.history_list,
        ):
            self._clear(box)

        graphics_count = sum(d["type"] == "gpu" for d in devices)
        network_count = sum(d["type"] == "network" for d in devices)
        active_count = sum(d["kernel"] != "Not loaded" for d in devices)
        attention = len(devices) - active_count
        if nvidia_status and nvidia_status["status"] in (
            "installed-not-active", "kernel-active", "module-loaded"
        ):
            attention += 1
        for key, value in (
            ("devices", len(devices)), ("drivers", active_count),
            ("attention", attention),
        ):
            self.metrics_labels[key].set_text(str(value))
        self.hero_description.set_text(
            self.os_info.get("PRETTY_NAME", "Linux")
            + "  ·  Kernel " + details["kernel"]["release"]
        )
        status = (f"{graphics_count} graphics · {network_count} network adapters"
                  if devices else "No supported PCI devices detected.")
        if not self.supported_host:
            status += (
                "\nRead-only mode: package operations are disabled outside Debian."
            )
            self.host_badge.set_text(
                self.os_info.get("PRETTY_NAME", "Linux") + "\nRead-only mode"
            )
        self.summary_label.set_text(status)

        for device in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("device-card")
            device_icon = Gtk.Image.new_from_icon_name(
                "video-display-symbolic" if device["type"] == "gpu"
                else "network-wireless-symbolic"
            )
            device_icon.set_pixel_size(21)
            row.append(device_icon)
            description = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            description.set_hexpand(True)
            device_name = Gtk.Label(
                label=device["name"], xalign=0, wrap=True
            )
            device_name.add_css_class("device-name")
            description.append(device_name)
            driver = Gtk.Label(
                label="Kernel: " + device["kernel"], xalign=0, wrap=True
            )
            driver.add_css_class("subdued")
            description.append(driver)
            row.append(description)
            row.append(self.create_status_widget(
                device, device["vendor"] == "10de" and device["type"] == "gpu"
            ))
            self.overview_list.append(row)

            card = self.create_device_card(device)
            self.device_card_map.append((device, card))
            target = (self.graphics_list if device["type"] == "gpu"
                      else self.network_list)
            target.append(card)

        if graphics_count == 0:
            self.graphics_list.append(
                self._notice("No PCI graphics devices detected.")
            )
        if network_count == 0:
            self.network_list.append(
                self._notice("No PCI network adapters detected.")
            )
        self.populate_diagnostics()
        self.update_global_status()
        self.set_busy(False)
        return False

    def populate_diagnostics(self):
        gpu = self.details.get("gpu", {})
        if gpu.get("available"):
            for entry in gpu.get("devices", []):
                self.gpu_metrics_box.append(self._info_card(
                    entry.get("name", "NVIDIA GPU"), (
                        ("Driver", entry.get("driver_version", "Not reported")),
                        ("VRAM", entry.get("memory.total", "N/A") + " MiB"),
                        ("Temperature", entry.get("temperature.gpu", "N/A") + " °C"),
                        ("Utilization", entry.get("utilization.gpu", "N/A") + " %"),
                        ("Power draw", entry.get("power.draw", "N/A") + " W"),
                    ), accent=True,
                ))
        else:
            self.gpu_metrics_box.append(self._notice(
                "NVIDIA telemetry: " + gpu.get("reason", "Unavailable")
            ))

        firmware = self.details.get("firmware", {})
        if firmware.get("state") == "ok":
            found = firmware.get("devices", [])
            if not found:
                self.firmware_list.append(
                    self._notice("fwupd found no supported firmware devices.")
                )
            for item in found:
                self.firmware_list.append(self._info_card(
                    item["name"], (
                        ("Firmware version", item["version"]),
                        ("Vendor", item["vendor"]),
                    ),
                ))
        else:
            self.firmware_list.append(self._notice(
                firmware.get("message", "Firmware inventory unavailable.")
            ))

        messages = self.details.get("messages", {})
        if messages.get("state") != "ok":
            self.firmware_messages_box.append(self._notice(
                messages.get("message", "Kernel messages unavailable.")
            ))
        elif not messages.get("messages"):
            self.firmware_messages_box.append(self._notice(
                "No matching firmware failures in the recent readable journal. "
                "This does not prove all firmware is installed."
            ))
        else:
            for item in messages["messages"]:
                self.firmware_messages_box.append(self._notice(item))

        kernel = self.details.get("kernel", {})
        self.kernel_list.append(self._info_card(
            "Running kernel", (
                ("Release", kernel.get("release", "Unknown")),
                ("Matching headers", (
                    "Present" if kernel.get("headers") else "Not detected"
                )),
                ("Secure Boot", kernel.get("secure_boot", "Unknown")),
            ),
        ))
        dkms = kernel.get("dkms", {})
        self.kernel_list.append(self._info_card(
            "DKMS for running kernel", (
                ("Status", dkms.get("state", "Unknown")),
                ("Details", dkms.get("text", "Unavailable")[:1200]),
            ),
        ))
        if self.nvidia_status:
            nvidia = self.nvidia_status
            self.kernel_list.append(self._info_card(
                "NVIDIA compatibility signals", (
                    ("Package", nvidia["package_name"]),
                    ("Kernel module", str(nvidia["kernel_driver"] or "Not bound")),
                    ("NVIDIA-SMI", "Working" if nvidia["nvidia_smi"] else "Unavailable"),
                    ("APT candidate", nvidia["candidate_version"]),
                ), accent=True,
            ))
        history = read_history()
        if not history:
            self.history_list.append(self._notice(
                "No driver operations recorded in this user account."
            ))
        for entry in history:
            self.history_list.append(self._info_card(
                entry.get("action", "Driver action"),
                (("Time (UTC)", entry.get("when", "Unknown")),
                 ("Result", "Completed" if entry.get("success") else "Failed")),
            ))


    # ========================================================
    # DEVICE CARD
    # ========================================================

    def create_device_card(self, device):
        is_nvidia = device["vendor"] == "10de" and device["type"] == "gpu"
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        card.add_css_class("device-card")
        if is_nvidia:
            card.add_css_class("nvidia-card")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_icon_name(
            "video-display-symbolic" if device["type"] == "gpu"
            else "network-wireless-symbolic"
        )
        icon.set_pixel_size(23)
        icon.set_valign(Gtk.Align.START)
        icon.add_css_class("device-icon")
        top.append(icon)
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        name_box.set_hexpand(True)
        name = Gtk.Label(label=device["name"], xalign=0, wrap=True)
        name.add_css_class("device-name")
        name_box.append(name)
        driver = Gtk.Label(label=device["driver_name"], xalign=0, wrap=True)
        driver.add_css_class("driver-name")
        name_box.append(driver)
        kernel = Gtk.Label(
            label=f"Kernel module: {device['kernel']}", xalign=0, wrap=True
        )
        kernel.add_css_class("kernel-text")
        name_box.append(kernel)
        modules = ", ".join(device.get("modules", [])) or "Not reported"
        alternatives = Gtk.Label(
            label="Available kernel modules: " + modules,
            xalign=0, wrap=True,
        )
        alternatives.add_css_class("kernel-text")
        name_box.append(alternatives)
        top.append(name_box)
        status = self.create_status_widget(device, is_nvidia)
        status.set_valign(Gtk.Align.START)
        top.append(status)
        card.append(top)

        if device["pkg"]:
            card.append(Gtk.Separator())
            versions = Gtk.Grid(column_spacing=16, row_spacing=7)
            versions.set_hexpand(True)
            for index, (title, value) in enumerate((
                ("Installed", device["installed_version"]),
                ("APT candidate", device["candidate_version"]),
                ("Newest in sources", device["newest_version"]),
                ("Source", device["source"]),
            )):
                label = Gtk.Label(label=title, xalign=0)
                label.add_css_class("version-label")
                versions.attach(label, 0, index, 1, 1)
                version = Gtk.Label(label=value, xalign=0, wrap=True)
                version.set_selectable(True)
                version.add_css_class("version-value")
                versions.attach(version, 1, index, 1, 1)
            card.append(versions)

        # The same NVIDIA package controls every NVIDIA card: only one switch.
        package = device["pkg"]
        if package and self.supported_host and package not in self.switches:
            card.append(Gtk.Separator())
            package_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            text.set_hexpand(True)
            switch_label = Gtk.Label(
                label="NVIDIA driver package", xalign=0
            )
            switch_label.add_css_class("device-name")
            text.append(switch_label)
            hint = Gtk.Label(
                label="Changes affect every GPU using this package.",
                xalign=0, wrap=True,
            )
            hint.add_css_class("driver-name")
            text.append(hint)
            package_row.append(text)
            toggle = Gtk.Switch(active=device["installed"])
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.connect("notify::active", self.update_action_state)
            self.switches[package] = toggle
            self.original_state[package] = device["installed"]
            package_row.append(toggle)
            card.append(package_row)

        if is_nvidia and not self.supported_host:
            card.append(self._notice(
                "Read-only on " + self.os_info.get("PRETTY_NAME", "this distribution")
                + ": use your distribution's driver manager for installation "
                  "and repair."
            ))

        if (is_nvidia and self.supported_host and self.nvidia_status
                and not self._nvidia_actions_added):
            self._nvidia_actions_added = True
            nvidia = self.nvidia_status
            note = None
            if "enabled" in nvidia["secure_boot"].lower():
                note = ("Secure Boot is enabled. Unsigned NVIDIA DKMS modules "
                        "may require enrollment or signing.")
            elif not nvidia["headers_installed"]:
                note = (f"Headers for {nvidia['kernel_release']} are missing; "
                        "the matching package must be available to rebuild DKMS.")
            elif nvidia["status"] == "nouveau":
                note = ("Nouveau currently owns this GPU. Installing NVIDIA "
                        "may require a reboot before the driver switches.")
            if note:
                warning = Gtk.Label(label=note, xalign=0, wrap=True)
                warning.add_css_class("kernel-text")
                card.append(warning)
            if nvidia["has_update"] or (
                nvidia["installed"] and nvidia["status"] not in ("active", "nouveau")
            ):
                actions = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=8
                )
                actions.add_css_class("action-row")
                if nvidia["has_update"]:
                    update = Gtk.Button(label="Install APT candidate")
                    update.add_css_class("suggested-action")
                    update.connect("clicked", self.on_nvidia_update)
                    self.action_buttons.append(update)
                    actions.append(update)
                if nvidia["installed"] and nvidia["status"] not in ("active", "nouveau"):
                    repair = Gtk.Button(label="Rebuild NVIDIA module")
                    repair.connect("clicked", self.on_nvidia_repair)
                    self.action_buttons.append(repair)
                    actions.append(repair)
                card.append(actions)

        return card

    # ========================================================
    # STATUS WIDGET (uses cached self.nvidia_status)
    # ========================================================

    def create_status_widget(self, device, is_nvidia):
        if is_nvidia and self.nvidia_status:
            status = self.nvidia_status["status"]
            label, css = {
                "active": ("● NVIDIA active", "status-ok"),
                "kernel-active": ("! SMI unavailable", "status-warning"),
                "nouveau": ("● Nouveau in use", "status-warning"),
                "module-loaded": ("! Check GPU binding", "status-warning"),
                "installed-not-active": ("! Driver inactive", "status-warning"),
                "not-installed": ("! Not installed", "status-error"),
            }.get(status, ("! Unknown", "status-warning"))
        elif device["pkg"]:
            label, css = (
                ("● Package installed", "status-ok") if device["installed"]
                else ("! Not installed", "status-warning")
            )
        elif device["kernel"] != "Not loaded":
            label, css = "● Kernel driver", "status-ok"
        else:
            label, css = "! No kernel driver", "status-warning"
        widget = Gtk.Label(label=label)
        widget.add_css_class(css)
        return widget

    # ========================================================
    # GLOBAL STATUS (uses cached self.nvidia_status)
    # ========================================================

    def update_global_status(self):

        if not self.nvidia_status:

            self.bottom_status.set_text(
                "Scan complete. Check each device's kernel module above."
            )

            return

        nvidia = self.nvidia_status

        if nvidia["status"] == "active":

            if nvidia["has_update"]:

                self.bottom_status.set_text(
                    "NVIDIA is active. A newer APT candidate is available."
                )

            else:

                self.bottom_status.set_text(
                    "NVIDIA is active. No newer APT candidate was found."
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

    def update_action_state(self, *_args):
        pending = sum(
            toggle.get_active() != self.original_state.get(package, False)
            for package, toggle in self.switches.items()
        )
        self.apply_button.set_label(
            f"Review {pending} change{'s' if pending != 1 else ''}"
            if pending else "No pending changes"
        )
        self.apply_button.set_sensitive(
            self.supported_host and bool(pending) and not self.busy
        )

    def on_apply(self, _button):
        if not self.supported_host:
            return
        if self.busy:
            return
        changes = [
            ("install" if toggle.get_active() else "remove", package)
            for package, toggle in self.switches.items()
            if toggle.get_active() != self.original_state.get(package, False)
        ]
        if not changes:
            return
        descriptions = "\n".join(
            f"• {action.capitalize()} {package}"
            for action, package in changes
        )
        removing = any(action == "remove" for action, _ in changes)
        extra = (
            "\n\nRemoving a graphics driver may leave you without a "
            "working graphical session. APT's planned removals will "
            "be checked before proceeding."
            if removing else ""
        )
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Review driver changes",
            body=descriptions + extra,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply changes")
        dialog.set_default_response("cancel")
        if removing:
            dialog.set_response_appearance(
                "apply", Adw.ResponseAppearance.DESTRUCTIVE
            )
        dialog.connect(
            "response",
            lambda _dialog, response: (
                self._start_package_changes(changes)
                if response == "apply" else None
            ),
        )
        dialog.present()

    def _start_package_changes(self, changes):
        if not self.supported_host:
            return
        self.current_action = "; ".join(
            action + " " + package for action, package in changes
        )
        if self.busy:
            return
        self.set_busy(True)
        self.bottom_status.set_text("Checking APT's planned changes…")
        threading.Thread(
            target=self.execute_package_changes,
            args=(changes,),
            daemon=True,
        ).start()

    def execute_package_changes(self, changes):
        logs = []
        try:
            for action, package in changes:
                # Recheck actual package state since the previous scan.
                installed, _ = get_installed_package_version(package)
                if installed == (action == "install"):
                    continue
                base = ["/usr/bin/apt-get"]
                if action == "install":
                    command = ["--no-remove", "install", package]
                else:
                    command = ["remove", package]
                code, plan = run_command(
                    base + ["-s"] + command, timeout=120
                )
                logs.append(f"=== Planned: {action} {package} ===\n{plan}")
                if code != 0:
                    raise RuntimeError("APT simulation failed; no change was made.")
                if action == "remove":
                    removals = [
                        line.split()[1].split(":")[0]
                        for line in plan.splitlines()
                        if line.startswith("Remv ") and len(line.split()) >= 2
                    ]
                    extra = sorted(set(removals) - {package})
                    if extra:
                        raise RuntimeError(
                            "Removal blocked: APT would also remove "
                            + ", ".join(extra)
                            + ". Review these dependencies manually."
                        )
                self._report_operation(f"{action.capitalize()}ing {package}…")
                command = [
                    "pkexec", "/usr/bin/apt-get", "-y"
                ] + (["--no-remove"] if action == "install" else []) + [
                    action, package
                ]
                code, output = run_command(command, timeout=None)
                logs.append(output)
                if code != 0:
                    raise RuntimeError(
                        f"APT exited with status {code} for {package}."
                    )
        except Exception as exc:
            logs.append(str(exc))
            GLib.idle_add(
                self.operation_finished, False, "\n\n".join(logs)
            )
            return
        GLib.idle_add(
            self.operation_finished, True, "\n\n".join(logs)
        )

    def _report_operation(self, message):
        GLib.idle_add(self.bottom_status.set_text, message)

    # ========================================================
    # NVIDIA UPDATE
    # ========================================================

    def on_nvidia_update(self, _button):
        if not self.supported_host:
            return
        if self.busy or not self.nvidia_status:
            return
        nvidia = self.nvidia_status
        if not nvidia["has_update"]:
            return
        candidate = nvidia["candidate_version"]
        newest = nvidia["newest_version"]
        backports_note = (
            f"\n\nThe newest version seen in your sources is {newest}, "
            "but APT does not select it by default. DebGear will not "
            "silently change repository priorities."
            if newest != candidate else ""
        )
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Install the NVIDIA APT candidate?",
            body=(
                f"Installed: {nvidia['installed_version']}\n"
                f"APT candidate: {candidate}\n\n"
                "APT will simulate the upgrade and refuse unexpected "
                "package removals before requesting administrator access."
                + backports_note
                + "\n\nA reboot may be required to load the new module."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("update", "Install update")
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            self.nvidia_update_response,
            candidate,
        )
        dialog.present()

    def nvidia_update_response(self, _dialog, response, version):
        if not self.supported_host or response != "update" or self.busy:
            return
        self.current_action = "Update NVIDIA to APT candidate " + version
        self.set_busy(True)
        self.bottom_status.set_text("Checking NVIDIA upgrade plan…")
        threading.Thread(
            target=self.execute_nvidia_update,
            args=(version,),
            daemon=True,
        ).start()

    def execute_nvidia_update(self, version):
        current = get_apt_candidate("nvidia-driver")
        if current != version:
            GLib.idle_add(
                self.operation_finished, False,
                f"APT candidate changed from {version} to {current}. Refresh first.",
            )
            return
        command = [
            "/usr/bin/apt-get", "-s", "--no-remove", "install", "nvidia-driver"
        ]
        code, plan = run_command(command, timeout=120)
        if code:
            GLib.idle_add(
                self.operation_finished, False,
                f"APT simulation failed:\n{plan}",
            )
            return
        self._report_operation("Installing NVIDIA candidate…")
        code, output = run_command([
            "pkexec", "/usr/bin/apt-get", "-y", "--no-remove",
            "install", "nvidia-driver"
        ], timeout=None)
        GLib.idle_add(
            self.operation_finished, code == 0,
            plan + "\n\n" + output,
        )

    # ========================================================
    # NVIDIA REPAIR
    # ========================================================

    def on_nvidia_repair(self, _button):
        if not self.supported_host:
            return
        if self.busy or not self.nvidia_status:
            return
        nvidia = self.nvidia_status
        if not nvidia["installed"]:
            self.show_dialog(
                "Driver not installed",
                "Install the NVIDIA package before attempting a DKMS rebuild.",
            )
            return
        if nvidia["status"] == "nouveau":
            self.show_dialog(
                "Nouveau is in use",
                "The Nouveau module currently owns the GPU. Do not forcibly "
                "unload it from a running graphical session. Reboot after "
                "configuring NVIDIA, then inspect the driver state again.",
            )
            return
        warning = (
            "\n\nSecure Boot is enabled: an unsigned module may be "
            "rejected. DKMS rebuilding does not enroll a signing key."
            if "enabled" in nvidia["secure_boot"].lower() else ""
        )
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Rebuild NVIDIA for this kernel?",
            body=(
                f"Kernel: {nvidia['kernel_release']}\n"
                f"Matching headers installed: "
                f"{'yes' if nvidia['headers_installed'] else 'no'}\n\n"
                "Install the matching headers and DKMS if available, rebuild "
                "for the running kernel, then attempt to load NVIDIA. "
                "This does not change kernels or repository priorities."
                + warning
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("repair", "Rebuild module")
        dialog.set_default_response("cancel")
        dialog.connect("response", self.nvidia_repair_response)
        dialog.present()

    def nvidia_repair_response(self, _dialog, response):
        if not self.supported_host or response != "repair" or self.busy:
            return
        self.current_action = "Rebuild NVIDIA module for " + os.uname().release
        self.set_busy(True)
        self.bottom_status.set_text("Preparing the NVIDIA DKMS rebuild…")
        threading.Thread(
            target=self.execute_nvidia_repair, daemon=True
        ).start()

    def execute_nvidia_repair(self):
        kernel = os.uname().release
        headers = f"linux-headers-{kernel}"
        installed, _ = get_installed_package_version(headers)
        if not installed and get_apt_candidate(headers) == "Unknown":
            GLib.idle_add(
                self.operation_finished, False,
                f"No installable matching headers for {kernel}. "
                "Select a kernel with available headers or configure "
                "the correct Debian repositories before rebuilding.",
            )
            return

        # Static command, no untrusted shell interpolation. Never unload
        # Nouveau or force-load a module while it owns the display.
        repair_script = r"""
set -eu
echo "=== Matching headers and DKMS ==="
apt-get -y --no-remove install dkms "linux-headers-$(uname -r)"
echo "=== Build modules for the running kernel ==="
dkms autoinstall -k "$(uname -r)"
echo "=== Refresh module dependencies ==="
depmod -a "$(uname -r)"
echo "=== Check GPU ownership ==="
if lspci -nnk | grep -A 3 -i 'NVIDIA' | grep -q 'Kernel driver in use: nouveau'; then
    echo 'Nouveau owns the GPU. A reboot or manual driver selection is needed.'
    exit 1
fi
echo "=== Load NVIDIA ==="
modprobe nvidia
echo "=== Verify NVIDIA userspace ==="
nvidia-smi
"""
        code, output = run_command(
            ["pkexec", "/bin/bash", "-c", repair_script],
            timeout=None,
        )
        GLib.idle_add(self.operation_finished, code == 0, output)

    # ========================================================
    # BUSY (fix: now disables every tracked action button, not
    # just "Apply Changes" — prevents overlapping pkexec calls)
    # ========================================================

    def set_busy(self, busy):
        self.busy = busy
        for button in self.action_buttons:
            button.set_sensitive(not busy)
        for toggle in self.switches.values():
            toggle.set_sensitive(not busy)
        self.update_action_state()

    def operation_finished(self, success, output):
        record_operation(self.current_action, success)
        # Refresh even after partial failure: one or more packages may have
        # changed before a later command failed.
        self.bottom_status.set_text(
            "APT operation finished; checking driver state…"
            if success else "Operation failed; refreshing package state…"
        )
        self.start_refresh(force=True)
        if success:
            self.show_dialog(
                "Operation finished",
                "APT completed the requested steps. DebGear is scanning "
                "the live kernel driver now. A reboot may still be required.",
            )
        else:
            if len(output) > 9000:
                output = output[-9000:]
            self.show_dialog(
                "Operation failed",
                "The operation did not complete. Details:\n\n"
                + (output or "No command output was returned."),
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
