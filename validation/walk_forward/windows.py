from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    def train(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.iloc[self.train_start : self.train_end].reset_index(drop=True)

    def test(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.iloc[self.test_start : self.test_end].reset_index(drop=True)


def make_walk_forward_windows(frame: pd.DataFrame, train_fraction: float, test_fraction: float) -> list[WalkForwardWindow]:
    rows = len(frame)
    train_size = max(int(rows * train_fraction), 1)
    test_size = max(int(rows * test_fraction), 1)
    step = test_size
    windows: list[WalkForwardWindow] = []
    start = 0
    index = 0
    while start + train_size + test_size <= rows:
        windows.append(
            WalkForwardWindow(
                index=index,
                train_start=start,
                train_end=start + train_size,
                test_start=start + train_size,
                test_end=start + train_size + test_size,
            )
        )
        start += step
        index += 1
    return windows
