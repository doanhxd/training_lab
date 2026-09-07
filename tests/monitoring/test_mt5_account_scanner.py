from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from training_lab.monitoring.rsiqui.mt5_account_scanner import Mt5TerminalCandidate, open_remote_desktop


class Mt5AccountScannerTests(TestCase):
    def test_candidate_display_name_contains_identity_and_terminal(self) -> None:
        candidate = Mt5TerminalCandidate(
            path=Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
            source="Cài đặt local",
            login="12345",
            name="Test Account",
            server="Exness-MT5Real",
            status="Đã đọc read-only",
        )
        self.assertIn("12345 / Exness-MT5Real", candidate.display_name)
        self.assertEqual("Test Account", candidate.name)
        self.assertIn("terminal64.exe", candidate.display_name)

    @patch("training_lab.monitoring.rsiqui.mt5_account_scanner.subprocess.Popen")
    def test_remote_desktop_never_receives_password(self, popen) -> None:
        open_remote_desktop("203.0.113.10", "Administrator")
        command = popen.call_args.args[0]
        self.assertEqual(["mstsc.exe", "/v:203.0.113.10", "/prompt"], command)
        self.assertNotIn("password", " ".join(command).lower())

    def test_remote_desktop_requires_host(self) -> None:
        with self.assertRaises(ValueError):
            open_remote_desktop("")
