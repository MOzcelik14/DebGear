# DebGear 0.3 — Driver Center

DebGear is a GTK 4 and Libadwaita **hardware and driver center** focused on Debian. It provides one place to inspect PCI graphics/network devices, kernel binding, NVIDIA driver state, installed APT versions, firmware inventory and kernel compatibility signals.

## What's new in 0.3

- Sidebar with six pages: **Overview, Graphics & GPU, Network, Firmware, Kernel & DKMS, Activity**.
- Dashboard totals for detected devices, bound kernel drivers and devices requiring inspection.
- Each GPU/network adapter shows the **active kernel driver** and **available kernel modules** reported by `lspci -nnk`. An available kernel module is **not** a guaranteed installable alternative.
- NVIDIA metrics using `nvidia-smi` where available: GPU name, driver version, VRAM, temperature, utilization and power draw. These are snapshots, not a monitoring daemon.
- Read-only `fwupdmgr get-devices --json` inventory. DebGear **does not flash firmware** or assert that a device has an update.
- Recent readable kernel-journal firmware errors. A restricted journal is reported as unavailable, never as healthy.
- Running kernel, matching headers, DKMS state, Secure Boot status and NVIDIA binding.
- Copyable, minimal diagnostic summary; private per-user driver action history under `~/.local/state/debgear/`.
- Supports reading installed NVIDIA metapackage names such as `nvidia-driver-580` on Linux Mint. **Package changes are disabled on Mint and other derivatives**; this is not yet a tested cross-distribution driver installer.

## Debian install

Download the `.deb` from the latest tagged release:

```bash
sudo apt install ./debgear_0.3.0_all.deb
debgear
```

DebGear uses system GTK4/GI, the host's APT and PolicyKit. The package depends on `python3`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `pciutils`, `pkexec`, and `apt`.

Optional read-only diagnostics: `fwupd` (`fwupdmgr`), `dkms`, `mokutil`, `nvidia-smi`.

### Run from source

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pciutils pkexec
git clone https://github.com/MOzcelik14/DebGear.git
cd DebGear
python3 debgear.py
```

### Build a native .deb

```bash
bash packaging/build-deb.sh 0.3.0
sudo apt install ./dist/debgear_0.3.0_all.deb
```

## Driver operations: read before use

DebGear offers driver package installation/removal, APT-candidate updates, and targeted NVIDIA DKMS repair **only when /etc/os-release reports ID=debian**. It does not silently opt into Backports.

- APT changes need confirmation, are simulated first, and block unexpected package removals.
- An NVIDIA DKMS rebuild targets **the running kernel** and requires matching headers available in APT.
- Nouveau is not forcibly unloaded from a running graphical session.
- Secure Boot may require module signing and MOK enrollment, which a rebuild alone does not solve.
- A successful APT transaction does **not** prove the GPU driver is active. The app rescans; a reboot may still be needed.
- Installed, APT candidate and newest package versions are separate fields.

**This release is not a universal driver recommendation engine.** Firmware installs, PRIME switching, automatic driver selection and cross-distro installs are not implemented; the relevant pages are diagnostic only.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile debgear.py debgear_diagnostics.py
bash packaging/build-deb.sh 0.3.0
```

CI also starts the GTK window in a virtual display and checks navigation/empty states.

MIT license.
