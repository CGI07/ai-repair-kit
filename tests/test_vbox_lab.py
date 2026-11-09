import unittest
from uuid import UUID

from scripts.check_vbox_lab import isolation_issues

VM_ID = UUID("53453a6f-c9ba-42ee-b2e4-d440c6213dd2")
ISOLATED = [
    f'UUID="{VM_ID}"',
    'VMState="poweroff"',
    'firmware="EFI64"',
    'SecureBoot="on"',
    'clipboard="disabled"',
    'clipboard_file_transfers="off"',
    'draganddrop="disabled"',
    'vrde="off"',
    'usb="off"',
    'ehci="off"',
    'xhci="off"',
    'audio="none"',
    *(f'nic{i}="none"' for i in range(1, 9)),
    *(f'uart{i}="off"' for i in range(1, 5)),
    *(f'lpt{i}="off"' for i in range(1, 3)),
]


class IsolationChecks(unittest.TestCase):
    def test_matches_the_lab_in_supported_states(self):
        for state in ("poweroff", "running", "saved"):
            with self.subTest(state=state):
                settings = [
                    f'VMState="{state}"' if line.startswith("VMState") else line
                    for line in ISOLATED
                ]
                self.assertEqual(isolation_issues(settings, VM_ID), [])
                self.assertTrue(isolation_issues(settings, UUID(int=0)))

    def test_refuses_transient_states_and_checks_saved_state_isolation(self):
        settings = [line for line in ISOLATED if not line.startswith(("VMState", "nic1="))]
        self.assertTrue(isolation_issues(settings + ['VMState="starting"', 'nic1="none"'], VM_ID))
        self.assertTrue(isolation_issues(settings + ['VMState="saved"', 'nic1="nat"'], VM_ID))

    def test_refuses_attached_or_ambiguous_channels(self):
        for key, value in (
            ("nic2", "nat"),
            ("clipboard", "hosttoguest"),
            ("draganddrop", "guesttohost"),
            ("clipboard_file_transfers", "on"),
            ("usb", "on"),
            ("vrde", "on"),
        ):
            with self.subTest(key=key):
                changed = [line for line in ISOLATED if not line.startswith(f"{key}=")]
                self.assertTrue(isolation_issues(changed, VM_ID))
                self.assertTrue(isolation_issues(changed + [f'{key}="{value}"'], VM_ID))
                self.assertTrue(isolation_issues(ISOLATED + [f'{key}="{value}"'], VM_ID))

    def test_refuses_a_host_share_or_an_extra_channel(self):
        for entry in ('SharedFolderPathGlobalMapping1="C:\\example"', 'nic9="none"'):
            with self.subTest(entry=entry):
                issues = isolation_issues(ISOLATED + [entry], VM_ID)
                self.assertTrue(issues)
                self.assertNotIn("example", str(issues))
