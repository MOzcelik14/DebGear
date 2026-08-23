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
        "name": "NVIDIA Sahipli Sürücü",
        "desc": "NVIDIA ekran kartları için kapalı kaynak sürücü."
    },

    "8086": {
        "pkg": None,
        "name": "Intel Açık Kaynak Sürücü",
        "desc": "Intel grafik donanımı Linux kernel ve Mesa tarafından destekleniyor."
    },

    "1002": {
        "pkg": None,
        "name": "AMD Açık Kaynak Sürücü",
        "desc": "AMD grafik donanımı Linux kernel ve Mesa tarafından destekleniyor."
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
    Debian sürüm karşılaştırması.

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
            "Kurulu değil"
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
            "Bilinmiyor"
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
    apt-cache policy içindeki Candidate değerini alır.

    LC_ALL=C kullanıldığı için sistem dili önemli değildir.
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
        return "Bilinmiyor"

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

    return "Bilinmiyor"


# ============================================================
# APT ALL AVAILABLE VERSIONS
# ============================================================

def get_apt_versions(package):

    """
    apt-cache madison ile APT'nin bildiği tüm sürümleri toplar.

    Örnek:

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
        return "Bilinmiyor"

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
    Sürümün hangi APT deposundan geldiğini bulmaya çalışır.
    """

    if not version:
        return "Bilinmiyor"

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
        return "Bilinmiyor"

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

        return "APT Deposu"

    return "Bilinmiyor"


# ============================================================
# PACKAGE INFORMATION
# ============================================================

def get_pkg_info(package):

    if not package:

        return {
            "installed": True,
            "installed_version": "Sistem tarafından sağlanıyor",
            "candidate_version": "Sistem tarafından sağlanıyor",
            "newest_version": "Sistem tarafından sağlanıyor",
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
    # nvidia-smi en güvenilir kullanıcı alanı kontrolü.
    # Çalışıyorsa NVIDIA sürücüsü aktif kabul edilir.
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
        and newest != "Bilinmiyor"
        and installed != "Bilinmiyor"
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

            # PCI class bilgisini temizle
            name = re.sub(
                r"^[0-9a-fA-F:.]+\s+",
                "",
                line
            )

            # [10de:xxxx] kısmını temizle
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

    for device in devices:

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
                    "Kernel Wi-Fi Sürücüsü"
                )

            else:

                driver_name = (
                    "Kernel Ağ Sürücüsü"
                )

            info = {
                "pkg": None,
                "name": driver_name,
                "desc": (
                    "Ağ donanımı Linux kernel "
                    "tarafından destekleniyor."
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
                    "name": "Kernel / Mesa Sürücüsü",
                    "desc": (
                        "Grafik donanımı Linux kernel "
                        "ve Mesa tarafından destekleniyor."
                    )
                }
            )

        package_info = get_pkg_info(
            info["pkg"]
        )

        result.append({
            "name": device["name"],
            "vendor": vendor,
            "device": device["device"],
            "type": device["type"],
            "pkg": info["pkg"],
            "driver_name": info["name"],
            "desc": info["desc"],
            "kernel": (
                device["kernel"]
                or "Yüklü değil"
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
        self.switches = {}

        self.setup_css()
        self.build_ui()
        self.refresh_devices()

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
            subtitle="Donanım ve sürücü yönetimi"
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
            label="Sistem Donanımları",
            xalign=0
        )

        page_title.add_css_class(
            "page-title"
        )

        page_description = Gtk.Label(
            label=(
                "Sisteminizde algılanan grafik ve ağ "
                "donanımlarını ve kullanılabilir sürücüleri "
                "görüntüleyin."
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
            label="0 donanım"
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
            label="DONANIMLAR",
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
            label="Sürücüler kontrol ediliyor...",
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
            label="Değişiklikleri Uygula"
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
    # REFRESH
    # ========================================================

    def refresh_devices(self):

        self.devices = scan_hardware()

        self.switches.clear()

        self.device_count.set_text(
            f"{len(self.devices)} donanım"
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
            label=f"Kernel sürücüsü: {device['kernel']}",
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
        # STATUS
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
            label="Mevcut:"
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
            label="APT adayı:"
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
            label="En yeni:"
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
        # NVIDIA ACTIONS
        # ----------------------------------------------------

        if is_nvidia:

            nvidia = get_nvidia_status()

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
                        f"Yeni NVIDIA sürümü mevcut: "
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
                    label="NVIDIA'yı Güncelle"
                )

                update_button.add_css_class(
                    "update-button"
                )

                update_button.connect(
                    "clicked",
                    self.on_nvidia_update
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
                        "NVIDIA kernel modülüyle ilgili "
                        "bir sorun tespit edildi."
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
                    label="NVIDIA Sürücüsünü Onar"
                )

                repair_button.add_css_class(
                    "repair-button"
                )

                repair_button.connect(
                    "clicked",
                    self.on_nvidia_repair
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
                label="Paketi etkin",
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

            self.switches[
                device["pkg"]
            ] = package_switch

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
    # STATUS WIDGET
    # ========================================================

    def create_status_widget(
        self,
        device,
        is_nvidia
    ):

        if is_nvidia:

            nvidia = get_nvidia_status()

            status = nvidia[
                "status"
            ]

            if status == "active":

                label = "✓ Sürücü aktif"
                css = "status-ok"

            elif status == "kernel-active":

                label = "⚠ Kernel sürücüsü aktif"
                css = "status-warning"

            elif status == "nouveau":

                label = "⚠ Nouveau aktif"
                css = "status-warning"

            elif status == "module-loaded":

                label = "⚠ NVIDIA modülü yüklü"
                css = "status-warning"

            elif status == "installed-not-active":

                label = "⚠ Sürücü aktif değil"
                css = "status-warning"

            else:

                label = "⚠ Sürücü kurulu değil"
                css = "status-error"

        elif device["pkg"]:

            if device["installed"]:

                label = "✓ Paket kurulu"
                css = "status-ok"

            else:

                label = "⚠ Paket kurulu değil"
                css = "status-warning"

        else:

            label = "✓ Sistem sürücüsü"
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
    # GLOBAL STATUS
    # ========================================================

    def update_global_status(self):

        nvidia_exists = any(
            device["vendor"] == "10de"
            and device["type"] == "gpu"
            for device in self.devices
        )

        if not nvidia_exists:

            self.bottom_status.set_text(
                "Sistem sürücüleri çalışır durumda."
            )

            return

        nvidia = get_nvidia_status()

        if nvidia["status"] == "active":

            if nvidia["has_update"]:

                self.bottom_status.set_text(
                    "NVIDIA aktif. Yeni sürücü sürümü mevcut."
                )

            else:

                self.bottom_status.set_text(
                    "NVIDIA sürücüsü aktif ve güncel."
                )

        elif nvidia["status"] == "kernel-active":

            self.bottom_status.set_text(
                "NVIDIA kernel sürücüsü aktif fakat NVIDIA-SMI çalışmıyor."
            )

        elif nvidia["status"] == "nouveau":

            self.bottom_status.set_text(
                "Nouveau aktif; NVIDIA sürücüsü kullanılmıyor."
            )

        elif nvidia["status"] == "module-loaded":

            self.bottom_status.set_text(
                "NVIDIA kernel modülü yüklü fakat sürücü bağlantısı kontrol edilmeli."
            )

        elif nvidia["status"] == "installed-not-active":

            self.bottom_status.set_text(
                "NVIDIA paketi kurulu fakat kernel sürücüsü aktif değil."
            )

        else:

            self.bottom_status.set_text(
                "NVIDIA sürücüsü kurulu değil."
            )

    # ========================================================
    # APPLY
    # ========================================================

    def on_apply(
        self,
        button
    ):

        changes = []

        for package, switch in (
            self.switches.items()
        ):

            installed, _ = (
                get_installed_package_version(
                    package
                )
            )

            active = switch.get_active()

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
                "Değişiklik yok",
                "Uygulanacak herhangi bir paket değişikliği bulunmuyor."
            )

            return

        self.set_busy(
            True
        )

        self.bottom_status.set_text(
            "Paket değişiklikleri uygulanıyor..."
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

        nvidia = get_nvidia_status()

        newest = nvidia[
            "newest_version"
        ]

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="NVIDIA sürücüsü güncellensin mi?",
            body=(
                f"Kurulu sürüm:\n"
                f"{nvidia['installed_version']}\n\n"
                f"Yeni sürüm:\n"
                f"{newest}\n\n"
                f"APT bu sürümü yapılandırılmış depolardan "
                f"kurmayı deneyecek."
            )
        )

        dialog.add_response(
            "cancel",
            "İptal"
        )

        dialog.add_response(
            "update",
            "Güncelle"
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
            "NVIDIA sürücüsü güncelleniyor..."
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
            heading="NVIDIA sürücüsü onarılsın mı?",
            body=(
                "APT güncellenecek, DKMS ve kernel header "
                "paketleri kontrol edilecek. NVIDIA kernel "
                "modülü yeniden oluşturulacak ve yüklenmeye "
                "çalışılacak."
            )
        )

        dialog.add_response(
            "cancel",
            "İptal"
        )

        dialog.add_response(
            "repair",
            "Onar"
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
            "NVIDIA sürücüsü onarılıyor..."
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
echo " NVIDIA SÜRÜCÜ ONARIMI"
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
echo " NVIDIA SÜRÜCÜSÜ BAŞARIYLA AKTİF"
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
    # BUSY
    # ========================================================

    def set_busy(
        self,
        busy
    ):

        self.apply_button.set_sensitive(
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

        self.set_busy(
            False
        )

        if success:

            self.bottom_status.set_text(
                "İşlem başarıyla tamamlandı."
            )

            self.refresh_devices()

            self.show_dialog(
                "İşlem tamamlandı",
                "Sürücü işlemi başarıyla tamamlandı."
            )

        else:

            self.bottom_status.set_text(
                "İşlem sırasında hata oluştu."
            )

            if len(output) > 6000:

                output = output[-6000:]

            self.show_dialog(
                "İşlem başarısız",
                "Komut çıktısı:\n\n" + output
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
            "Tamam"
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
