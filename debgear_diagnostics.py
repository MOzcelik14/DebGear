"""Read-only diagnostics for DebGear; never changes the host system."""
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

HISTORY_LIMIT = 120


def run_probe(argv, timeout=12):
    try:
        result = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=timeout, check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def operating_system():
    result = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def supports_driver_changes(os_info):
    """A Debian APT workflow is not automatically suitable for derivatives."""
    return os_info.get("ID", "").lower() == "debian"


def detect_nvidia_package():
    """Find installed Debian and Ubuntu/Mint NVIDIA metapackage names."""
    rc, output = run_probe([
        "dpkg-query", "-W",
        "-f=${binary:Package}\t${Version}\t${db:Status-Status}\n",
        "nvidia-driver*",
    ])
    if rc != 0:
        return None
    names = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or fields[2] != "installed":
            continue
        name = fields[0].split(":")[0]
        if name == "nvidia-driver" or re.fullmatch(
            r"nvidia-driver-[0-9]+(?:-(?:open|server))?", name
        ):
            names.append(name)
    if "nvidia-driver" in names:
        return "nvidia-driver"
    return sorted(names)[-1] if names else None


def query_gpu():
    if not shutil.which("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi is unavailable"}
    keys = [
        "name", "driver_version", "memory.total", "temperature.gpu",
        "utilization.gpu", "power.draw",
    ]
    code, output = run_probe([
        "nvidia-smi", "--query-gpu=" + ",".join(keys),
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        return {"available": False, "reason": output[:280] or "GPU query failed"}
    devices = []
    for line in output.splitlines():
        values = [part.strip() for part in line.split(",")]
        if len(values) != len(keys):
            continue
        devices.append(dict(zip(keys, values)))
    return {"available": bool(devices), "devices": devices}


def firmware_report():
    """fwupd inventory only; do not infer whether updates are available."""
    if not shutil.which("fwupdmgr"):
        return {
            "state": "missing",
            "message": "fwupd is not installed; firmware inventory is unavailable.",
            "devices": [],
        }
    code, output = run_probe(["fwupdmgr", "get-devices", "--json"], timeout=20)
    if code != 0:
        return {
            "state": "unavailable",
            "message": output[:320] or "fwupd could not inspect devices.",
            "devices": [],
        }
    try:
        payload = json.loads(output)
        devices = []
        for item in payload.get("Devices", []):
            if not isinstance(item, dict):
                continue
            devices.append({
                "name": str(item.get("Name") or "Unnamed device"),
                "version": str(item.get("Version") or "Not reported"),
                "vendor": str(item.get("Vendor") or "Not reported"),
            })
        return {"state": "ok", "message": "", "devices": devices}
    except (ValueError, AttributeError, TypeError):
        return {
            "state": "unavailable",
            "message": "fwupd returned an unreadable device inventory.",
            "devices": [],
        }


def kernel_report():
    release = platform.uname().release
    headers = Path("/lib/modules") / release / "build"
    dkms = {"state": "missing", "text": "dkms is not installed"}
    if shutil.which("dkms"):
        code, output = run_probe(["dkms", "status", "-k", release])
        dkms = {
            "state": "ok" if code == 0 else "unavailable",
            "text": output or ("No DKMS modules registered for this kernel"
                               if code == 0 else "DKMS status could not be read"),
        }
    secure_boot = "Unknown (mokutil unavailable)"
    if shutil.which("mokutil"):
        code, output = run_probe(["mokutil", "--sb-state"])
        secure_boot = output if code == 0 and output else "Unavailable"
    return {
        "release": release,
        "headers": headers.exists(),
        "dkms": dkms,
        "secure_boot": secure_boot,
    }


def firmware_messages():
    """Kernel logs may be restricted; unavailable is not a clean bill of health."""
    if not shutil.which("journalctl"):
        return {"state": "unavailable", "messages": [],
                "message": "journalctl is unavailable"}
    code, output = run_probe([
        "journalctl", "--kernel", "--boot", "--no-pager",
        "--output=cat", "--lines=350",
    ], timeout=12)
    if code or "No journal files were opened" in output:
        return {"state": "unavailable", "messages": [],
                "message": "Kernel journal access is unavailable"}
    matches = []
    for line in output.splitlines():
        if re.search(
            r"firmware.{0,80}(?:failed|missing|not found|error)"
            r"|direct firmware load.{0,100}failed",
            line, flags=re.I,
        ):
            matches.append(line.strip()[:300])
    return {"state": "ok", "messages": matches[-12:], "message": ""}


def history_path():
    return (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "debgear" / "history.jsonl")


def record_operation(action, success):
    """Record only an operation label and result, never privileged output."""
    entry = {
        "when": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": str(action)[:100],
        "success": bool(success),
    }
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        path.write_text(
            "\n".join((previous + [json.dumps(entry)])[-HISTORY_LIMIT:]) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError:
        pass


def read_history():
    try:
        lines = history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines[-HISTORY_LIMIT:]:
        try:
            row = json.loads(line)
            if isinstance(row, dict) and "action" in row:
                entries.append(row)
        except ValueError:
            continue
    return entries[::-1]
