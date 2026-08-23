# ⚙️ DebGear

**DebGear** is a lightweight graphical driver and hardware manager for Debian, built with **Python, GTK 4 and Libadwaita**.

It provides a simple way to inspect detected hardware, installed drivers, APT package versions and NVIDIA driver status without manually running multiple terminal commands.

## ✨ Features

- 🔍 Automatic hardware detection
- 🎮 NVIDIA GPU detection
- 🟢 NVIDIA driver status monitoring
- 📦 Installed driver version detection
- 📋 APT candidate version detection
- 🔎 Detection of the newest available package version
- 📚 Debian / Backports repository detection
- 🧩 DKMS support
- 🐧 Kernel header detection
- 🔧 NVIDIA driver repair
- 📈 NVIDIA driver update detection
- `nvidia-smi` verification
- GTK 4 + Libadwaita interface

## Requirements

DebGear is designed for Debian-based systems.

Required packages:

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adwaita-1 pciutils dkms
```

For NVIDIA functionality:

```bash
sudo apt install nvidia-driver
```

## Running

Clone the repository:

```bash
git clone https://github.com/MOzcelik14/DebGear.git
cd DebGear
```

Run:

```bash
python3 debgear.py
```

## NVIDIA Driver Detection

DebGear checks the actual NVIDIA driver state instead of only checking whether the package is installed.

It checks:

- NVIDIA kernel module
- Kernel driver in use
- `nvidia-smi`
- Installed package version
- APT candidate version
- Newest available version

## NVIDIA Repair

When the NVIDIA driver is not functioning correctly, DebGear can rebuild and reload the NVIDIA kernel module.

Administrative operations are executed through `pkexec`.

## Version Management

DebGear distinguishes between:

- **Installed** — currently installed version
- **APT Candidate** — version APT would normally install
- **Newest Available** — newest version found in configured APT repositories

This is especially useful when using Debian Backports.

## Development Status

DebGear is currently in early development.

The project is primarily designed for Debian and may require additional work before supporting other Debian-based distributions reliably.

Use driver management and repair functionality with care.

## 📄 License

DebGear is released under the MIT License.
