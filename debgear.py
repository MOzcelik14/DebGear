#!/usr/bin/env python3

import gi
import subprocess
import sys
import re
import threading
import os
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
    package = get_pkg_info("nvidia-driver")
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

        package = info["pkg"]
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
            .debgear-content { padding: 26px 8px 32px; }
            .hero {
                padding: 24px;
                border-radius: 18px;
                background: alpha(@accent_bg_color, 0.09);
                border: 1px solid alpha(@accent_bg_color, 0.20);
            }
            .hero-icon {
                color: @accent_color;
                background: alpha(@accent_bg_color, 0.14);
                border-radius: 14px;
                padding: 15px;
            }
            .page-title { font-size: 26px; font-weight: 800; }
            .page-description { color: alpha(@window_fg_color, 0.70); font-size: 13px; }
            .section-label {
                color: alpha(@window_fg_color, 0.67);
                font-weight: bold;
                font-size: 12px;
                margin-top: 12px;
            }
            .count-badge {
                border-radius: 99px;
                padding: 6px 12px;
                background: alpha(@window_fg_color, 0.07);
                font-weight: bold;
                font-size: 12px;
            }
            .device-card {
                padding: 20px;
                border-radius: 16px;
                background: @card_bg_color;
                border: 1px solid alpha(@window_fg_color, 0.09);
            }
            .nvidia-card { border-color: alpha(#76b900, 0.48); }
            .device-icon {
                border-radius: 12px;
                padding: 12px;
                background: alpha(@window_fg_color, 0.055);
            }
            .device-name { font-size: 15px; font-weight: bold; }
            .driver-name { color: alpha(@window_fg_color, 0.68); font-size: 12px; }
            .kernel-text { color: alpha(@window_fg_color, 0.65); font-size: 11px; }
            .version-label { color: alpha(@window_fg_color, 0.60); font-size: 11px; }
            .version-value { font-size: 12px; font-weight: bold; }
            .status-ok, .status-warning, .status-error, .status-update {
                border-radius: 99px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            .status-ok {
                color: #207b4c; background: alpha(#2ec27e, 0.16);
            }
            .status-warning {
                color: #9b6200; background: alpha(#f4b400, 0.17);
            }
            .status-error {
                color: #b52b31; background: alpha(#ed333b, 0.13);
            }
            .status-update {
                color: @accent_color; background: alpha(@accent_bg_color, 0.12);
            }
            .bottom-bar {
                padding: 12px 22px;
                border-top: 1px solid alpha(@window_fg_color, 0.09);
                background: @window_bg_color;
            }
            .bottom-status { color: alpha(@window_fg_color, 0.72); font-size: 12px; }
            .apply-button { min-height: 39px; min-width: 168px; }
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
            title="DebGear", subtitle="Debian hardware & drivers"
        ))
        self.refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Rescan hardware and driver packages")
        self.refresh_button.connect("clicked", lambda *_: self.start_refresh())
        header.pack_end(self.refresh_button)
        self.action_buttons.append(self.refresh_button)
        root.append(header)

        scroll = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        clamp = Adw.Clamp()
        clamp.set_maximum_size(1040)
        clamp.set_tightening_threshold(680)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.add_css_class("debgear-content")
        clamp.set_child(content)
        scroll.set_child(clamp)
        root.append(scroll)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.add_css_class("hero")
        hero_icon = Gtk.Image.new_from_icon_name("computer-symbolic")
        hero_icon.set_pixel_size(40)
        hero_icon.set_valign(Gtk.Align.START)
        hero_icon.add_css_class("hero-icon")
        hero.append(hero_icon)

        intro_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        intro_text.set_hexpand(True)
        title = Gtk.Label(label="Your hardware, at a glance", xalign=0, wrap=True)
        title.add_css_class("page-title")
        intro_text.append(title)
        description = Gtk.Label(
            label="Inspect kernel drivers, NVIDIA health and versions from your configured APT sources.",
            xalign=0, wrap=True,
        )
        description.add_css_class("page-description")
        intro_text.append(description)
        hero.append(intro_text)
        content.append(hero)

        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.summary_label = Gtk.Label(
            label="Scanning your system…", xalign=0, hexpand=True, wrap=True
        )
        self.summary_label.add_css_class("page-description")
        summary.append(self.summary_label)
        self.device_count = Gtk.Label(label="0 devices")
        self.device_count.add_css_class("count-badge")
        summary.append(self.device_count)
        content.append(summary)

        heading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading = Gtk.Label(label="DETECTED DEVICES", xalign=0, hexpand=True)
        heading.add_css_class("section-label")
        heading_row.append(heading)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter devices…")
        self.search_entry.set_width_chars(18)
        self.search_entry.connect("search-changed", self.on_search_changed)
        heading_row.append(self.search_entry)
        content.append(heading_row)

        self.device_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12
        )
        loading = Gtk.Label(label="Detecting PCI hardware and APT packages…")
        loading.set_margin_top(28)
        self.device_list.append(loading)
        content.append(self.device_list)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
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

    def on_search_changed(self, *_args):
        query = self.search_entry.get_text().strip().casefold()
        for device, card in self.device_card_map:
            haystack = " ".join(
                str(device.get(key, ""))
                for key in ("name", "driver_name", "kernel", "vendor", "device")
            ).casefold()
            card.set_visible(query in haystack)

    # ========================================================
    # REFRESH (fix: scanning now happens off the GTK thread)
    # ========================================================

    def start_refresh(self, force=False):
        if self.busy and not force:
            return
        self.set_busy(True)
        self.bottom_status.set_text("Checking hardware and installed drivers…")
        threading.Thread(target=self._scan_in_background, daemon=True).start()

    def _scan_in_background(self):
        try:
            if not shutil.which("lspci"):
                raise RuntimeError("Hardware scan requires pciutils (lspci).")
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
            GLib.idle_add(self._apply_refresh, devices, nvidia_status)
        except Exception as exc:
            GLib.idle_add(self._refresh_failed, str(exc))

    def _refresh_failed(self, message):
        self.bottom_status.set_text("Unable to scan hardware.")
        self.summary_label.set_text("Hardware scan failed")
        self.set_busy(False)
        self.show_dialog("Hardware Scan Failed", message)
        return False

    def _apply_refresh(self, devices, nvidia_status):
        self.devices = devices
        self.nvidia_status = nvidia_status
        self.switches.clear()
        self.original_state.clear()
        self.device_card_map.clear()
        self._nvidia_actions_added = False
        self.action_buttons = [self.refresh_button, self.apply_button]
        self.device_count.set_text(f"{len(devices)} devices")

        while self.device_list.get_first_child():
            self.device_list.remove(self.device_list.get_first_child())

        if not devices:
            empty = Gtk.Label(
                label="No supported PCI graphics or network devices were detected.",
                xalign=0, wrap=True,
            )
            empty.set_margin_top(24)
            self.device_list.append(empty)

        for device in devices:
            card = self.create_device_card(device)
            self.device_card_map.append((device, card))
            self.device_list.append(card)

        self.on_search_changed()
        self.update_global_status()
        self.summary_label.set_text(
            f"{sum(d['type'] == 'gpu' for d in devices)} graphics · "
            f"{sum(d['type'] == 'network' for d in devices)} network devices"
            if devices else "No matching hardware found"
        )
        self.set_busy(False)
        return False

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
        if package and package not in self.switches:
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

        if is_nvidia and self.nvidia_status and not self._nvidia_actions_added:
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
