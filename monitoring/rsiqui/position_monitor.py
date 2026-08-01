from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


RSIQUI_V3_COMMENT = "RSIQUI V3"
RUNNER_IDENTIFIERS = {
    "rsiqui_v3_ori": ("rsiqui_ori_demo.py", "trading_lab.runners.mt5.rsiqui_ori_demo", "rsiqui_ori_demo"),
    "rsiqui_v3_neg": ("rsiqui_neg_demo.py", "trading_lab.runners.mt5.rsiqui_neg_demo", "rsiqui_neg_demo"),
    "rsiqui_v3_final": ("rsiqui_final_demo.py", "trading_lab.runners.mt5.rsiqui_final_demo", "rsiqui_final_demo"),
}


@dataclass(frozen=True)
class PositionView:
    ticket: int
    side: str
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    profit: float
    source: str


@dataclass(frozen=True)
class RunnerView:
    strategy_key: str
    label: str
    pid: int
    command: str


@dataclass(frozen=True)
class MonitorSnapshot:
    login: int
    server: str
    balance: float
    equity: float
    currency: str
    positions: tuple[PositionView, ...]
    log_entries: tuple[str, ...]
    runner_detection_available: bool
    running_runners: tuple[RunnerView, ...]


class RsiquiV3PositionMonitor:
    """Read-only MT5 position monitor for the RSIQUI V3 demo runner.

    This class never authenticates, evaluates signals, sends orders, closes positions,
    or changes MT5 state. It reads the account currently connected in MT5 only.
    """

    def __init__(self, *, symbol: str, mt5: Any, process_iter: Callable[[], Iterable[Any]] | None = None) -> None:
        self.symbol = symbol
        self.mt5 = mt5
        self._connected = False
        self._previous_positions: dict[int, PositionView] = {}
        self._process_iter = process_iter or self._default_process_iter

    @staticmethod
    def _default_process_iter() -> Iterable[Any]:
        try:
            import psutil  # type: ignore
        except ImportError:
            return ()
        return psutil.process_iter(["pid", "cmdline", "name"])

    @staticmethod
    def _process_info(process: Any) -> tuple[int, list[str]]:
        info = getattr(process, "info", None)
        if isinstance(info, dict):
            pid = int(info.get("pid") or 0)
            cmdline = info.get("cmdline") or ()
        else:
            pid = int(getattr(process, "pid", 0) or 0)
            cmdline_method = getattr(process, "cmdline", None)
            cmdline = cmdline_method() if callable(cmdline_method) else ()
        return pid, [str(part) for part in cmdline if part]

    def _running_runners(self) -> tuple[bool, tuple[RunnerView, ...]]:
        try:
            processes = list(self._process_iter())
        except Exception:
            return False, ()
        detected: list[RunnerView] = []
        for process in processes:
            try:
                pid, cmdline_parts = self._process_info(process)
            except Exception:
                continue
            command = " ".join(cmdline_parts).lower()
            if not command:
                continue
            for strategy_key, identifiers in RUNNER_IDENTIFIERS.items():
                if any(identifier.lower() in command for identifier in identifiers):
                    detected.append(
                        RunnerView(
                            strategy_key=strategy_key,
                            label=strategy_key,
                            pid=pid,
                            command=" ".join(cmdline_parts),
                        )
                    )
                    break
        detected.sort(key=lambda runner: (runner.strategy_key, runner.pid))
        return True, tuple(detected)

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        if not self.mt5.initialize():
            raise RuntimeError(f"Không thể kết nối MT5: {self.mt5.last_error()}")
        self._connected = True

    def _position_view(self, position: Any) -> PositionView:
        side = "BUY" if position.type == self.mt5.POSITION_TYPE_BUY else "SELL"
        comment = str(getattr(position, "comment", "") or "").upper()
        source = "RSIQUI V3" if RSIQUI_V3_COMMENT in comment else "THỦ CÔNG"
        return PositionView(
            ticket=int(position.ticket),
            side=side,
            volume=float(position.volume),
            price_open=float(position.price_open),
            stop_loss=float(getattr(position, "sl", 0.0)),
            take_profit=float(getattr(position, "tp", 0.0)),
            profit=float(position.profit),
            source=source,
        )

    def refresh(self) -> MonitorSnapshot:
        self._ensure_connected()
        account = self.mt5.account_info()
        if account is None:
            raise RuntimeError("MT5 chưa có tài khoản đang đăng nhập.")
        raw_positions = self.mt5.positions_get(symbol=self.symbol) or ()
        positions = tuple(self._position_view(position) for position in raw_positions)
        current_positions = {position.ticket: position for position in positions}
        log_entries: list[str] = []
        for ticket, position in current_positions.items():
            if ticket not in self._previous_positions:
                log_entries.append(
                    f"{'🟢' if position.source == 'RSIQUI V3' else '•'} "
                    f"{position.source} mở {position.side} #{ticket} | "
                    f"{position.volume:.2f} lot @ {position.price_open:.2f}"
                )
        for ticket, position in self._previous_positions.items():
            if ticket not in current_positions:
                log_entries.append(f"⚪ {position.source} #{ticket} đã đóng / không còn hiển thị trên MT5.")
        self._previous_positions = current_positions
        runner_detection_available, running_runners = self._running_runners()
        return MonitorSnapshot(
            login=int(account.login),
            server=str(account.server),
            balance=float(account.balance),
            equity=float(account.equity),
            currency=str(account.currency),
            positions=positions,
            log_entries=tuple(log_entries),
            runner_detection_available=runner_detection_available,
            running_runners=running_runners,
        )

    def stop(self) -> None:
        if self._connected:
            self.mt5.shutdown()
        self._connected = False
