# DebGear

A GTK 4 + Libadwaita hardware and driver dashboard for **Debian-based systems**. DebGear reads the **host system's** PCI, kernel, dpkg and APT state; it does not download drivers from third-party sites or alter repository priorities.

## Dashboard

- Searchable graphics and network device cards with kernel module details.
- NVIDIA kernel binding, userspace `nvidia-smi`, running kernel, Secure Boot status (when `mokutil` is available), header and DKMS diagnostics.
- Distinct **installed version**, **APT candidate** (what a normal installation would select), and **newest version found in configured sources**. A newer Backports package is informational if it is not the selected candidate.
- Adaptive light and dark appearance with a refreshed application icon.
- Background hardware scans, a dedicated rescan control, and reviewable driver changes.

## Install

Download the native `.deb` from a tagged GitHub Release, then run:

```bash
sudo apt install ./debgear_VERSION_all.deb
debgear
```

The package installs the desktop launcher, SVG icon and Python application, and depends on Debian's system GTK/Libadwaita bindings, `pciutils`, `apt` and `pkexec`. This native package replaces earlier incomplete AppImage recipes: driver management needs the host's APT, kernel and PolicyKit integration.

### Run from source

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pciutils pkexec
git clone https://github.com/MOzcelik14/DebGear.git
cd DebGear
python3 debgear.py
```

Optional diagnostics: `dkms`, `mokutil`, `nvidia-smi`. No NVIDIA package is required simply to inspect hardware.

### Build a .deb locally

```bash
bash packaging/build-deb.sh 0.2.0
sudo apt install ./dist/debgear_0.2.0_all.deb
```

CI checks Python syntax, basic backend regressions, the desktop entry, the packaging script, and the resulting Debian package.

## Driver safety

- **Apply Changes** previews the selected packages and requires confirmation.
- APT installs and updates are simulated and use `--no-remove`; DebGear blocks removal if the simulation plans to remove packages besides the selected driver package.
- NVIDIA updates follow **APT candidate**, not the highest version in all repositories. DebGear never silently opts into Backports.
- Repair uses matching headers for the **running kernel** and a targeted DKMS rebuild. If those headers are not installed or available in APT, it stops and explains why.
- Repair will not forcibly unload Nouveau. Enabled Secure Boot may require DKMS module signing and MOK enrollment; rebuilding alone does not solve that.
- A successful APT transaction is **not** proof that the driver has become active. DebGear scans again after an operation; you may need to reboot.

**Important:** Driver removal or module changes can affect a working desktop session. Review the package plan and keep a known-working kernel available. On unsupported, custom or very new kernels, matching headers and NVIDIA DKMS compatibility are not guaranteed.

## Development

```bash
python3 -m py_compile debgear.py tests/test_core.py
python3 -m unittest discover -s tests -v
```

MIT license.
