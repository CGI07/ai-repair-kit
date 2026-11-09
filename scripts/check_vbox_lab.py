"""Check the VirtualBox host settings of the isolated Windows test lab."""

import argparse
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID


def isolation_issues(lines: Iterable[str], vm_id: UUID) -> list[str]:
    values: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep:
            values[key.strip('"').casefold()].append(value)

    expected = {
        "uuid": str(vm_id),
        "firmware": "EFI64",
        "secureboot": "on",
        "clipboard": "disabled",
        "clipboard_file_transfers": "off",
        "draganddrop": "disabled",
        "vrde": "off",
        "usb": "off",
        "ehci": "off",
        "xhci": "off",
        "audio": "none",
        **{f"nic{i}": "none" for i in range(1, 9)},
        **{f"uart{i}": "off" for i in range(1, 5)},
        **{f"lpt{i}": "off" for i in range(1, 3)},
    }
    issues = [
        f"{key} is missing, ambiguous, or has an unexpected value"
        for key, value in expected.items()
        if values[key] != [f'"{value}"']
    ]
    if values["vmstate"] not in (['"poweroff"'], ['"running"'], ['"saved"']):
        issues.append("VM is not powered off, running, or saved")
    if any(key.startswith("sharedfolder") for key in values):
        issues.append("A host share is present")
    if any(re.fullmatch(r"(nic|uart|lpt)\d+", key) and key not in expected for key in values):
        issues.append("An unexpected hardware channel is present")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vm_id", type=UUID)
    options = parser.parse_args()
    issues = isolation_issues(sys.stdin, options.vm_id)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("Host settings passed. Check virtual media, the guest, and snapshots separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
