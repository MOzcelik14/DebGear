#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-0.3.0}"
OUT="${2:-${ROOT}/dist}"

if [[ ! "$VERSION" =~ ^[0-9][A-Za-z0-9.+:~\-]*$ ]]; then
  echo "Invalid Debian version: $VERSION" >&2
  exit 2
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"

install -Dm755 "$ROOT/debgear.py" "$STAGE/usr/lib/debgear/debgear.py"
install -Dm644 "$ROOT/debgear_diagnostics.py" "$STAGE/usr/lib/debgear/debgear_diagnostics.py"
install -Dm644 "$ROOT/data/com.mozcelik.DebGear.desktop" "$STAGE/usr/share/applications/com.mozcelik.DebGear.desktop"
install -Dm644 "$ROOT/data/com.mozcelik.DebGear.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/com.mozcelik.DebGear.svg"
install -Dm644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/debgear/copyright"

mkdir -p "$STAGE/usr/bin" "$STAGE/DEBIAN"
cat > "$STAGE/usr/bin/debgear" <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/debgear/debgear.py "$@"
LAUNCHER
chmod 755 "$STAGE/usr/bin/debgear"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: debgear
Version: ${VERSION}
Section: admin
Priority: optional
Architecture: all
Maintainer: DebGear contributors <MOzcelik14@users.noreply.github.com>
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pciutils, pkexec, apt
Description: Debian hardware and driver manager
 Inspect PCI graphics and network hardware, installed packages, APT
 candidates, NVIDIA kernel state and DKMS diagnostics using GTK4.
EOF

dpkg-deb --root-owner-group --build "$STAGE" "$OUT/debgear_${VERSION}_all.deb"
echo "Built $OUT/debgear_${VERSION}_all.deb"
