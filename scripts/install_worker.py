from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys


LABEL = "com.maxtrade.paper-worker"


def service_definition(root: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(root / ".venv/bin/python"), "-m", "maxtrade.worker", "--watch",
                             "--database", str(root / "data/scan_history.sqlite3"),
                             "--secrets-file", str(root / ".streamlit/secrets.toml"),
                             "--require-integrations"],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"MAXTRADE_DATABASE": str(root / "data/scan_history.sqlite3"),
                                 "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "StandardOutPath": str(root / "data/worker.stdout.log"),
        "StandardErrorPath": str(root / "data/worker.stderr.log"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the macOS paper-only worker LaunchAgent")
    parser.add_argument("action", choices=["install", "status", "stop", "restart"])
    arguments = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("This installer requires macOS; use a managed service on a Linux VPS")
    root = Path(__file__).resolve().parents[1]
    target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    if arguments.action == "install":
        target.parent.mkdir(parents=True, exist_ok=True)
        (root / "data").mkdir(exist_ok=True)
        with target.open("wb") as output:
            plistlib.dump(service_definition(root), output)
        target.chmod(0o600)
        subprocess.run(["launchctl", "bootout", service], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        print("Paper worker installed. Private local Secrets required; Mac must remain awake and online.")
    elif arguments.action == "stop":
        subprocess.run(["launchctl", "bootout", service], check=True)
    elif arguments.action == "restart":
        subprocess.run(["launchctl", "kickstart", "-k", service], check=True)
    else:
        subprocess.run(["launchctl", "print", service], check=True)


if __name__ == "__main__":
    main()