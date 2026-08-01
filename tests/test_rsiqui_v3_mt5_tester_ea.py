from __future__ import annotations

import unittest
from pathlib import Path


EA_PATH = Path(r"C:\Users\Maple Razer\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\RSIQUI_V3_Causal30_Tester.mq5")


class RsiquiV3Mt5TesterEaContractTests(unittest.TestCase):
    def test_blocks_entries_at_gmt7_execution_windows_before_submit(self) -> None:
        if not EA_PATH.exists():
            self.skipTest(f"EA source not present on this machine: {EA_PATH}")
        source = EA_PATH.read_text(encoding="utf-8")

        self.assertIn("input bool   InpEnableGmt7EntryBlackout = true;", source)
        self.assertIn("input int    InpServerToGmt7Hours = 7;", source)
        self.assertIn("bool IsGmt7EntryBlackout(datetime entry_bar_time)", source)
        self.assertIn("hour>=5 && hour<8", source)
        self.assertIn("hour>=20 && hour<22", source)
        self.assertIn("if(IsGmt7EntryBlackout(entry_bar_time)) return;", source)
        self.assertLess(source.index("if(IsGmt7EntryBlackout(entry_bar_time)) return;"), source.index("SubmitCausalOrder(signal)"))


if __name__ == "__main__":
    unittest.main()
