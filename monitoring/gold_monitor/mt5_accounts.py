from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any


@dataclass(frozen=True)
class Mt5TerminalCandidate:
    path: Path
    source: str
    login: str = ""
    name: str = ""
    server: str = ""
    currency: str = ""
    equity: float | None = None


# TEMPORARY GOLD Monitor allowlist. Restore discovery after account review.
FIXED_MONITORED_TERMINALS: tuple[tuple[str, str, str, str], ...] = (
    (r"C:\Program Files\MetaTrader 5\terminal64.exe", "263555815", "BOT VIP 1", "Exness-MT5Real37"),
    (r"C:\Program Files\MetaTrader 5_2\terminal64.exe", "263557311", "BOT VIP 2", "Exness-MT5Real37"),
    (r"C:\Program Files\MetaTrader 5_3\terminal64.exe", "257536208", "BOT VIP 3", "Exness-MT5Real36"),
    (r"C:\Program Files\MetaTrader 5_5_Mom\terminal64.exe", "184127910", "BOT VIP 4", "Exness-MT5Real25"),
)


def load_fixed_candidates() -> tuple[Mt5TerminalCandidate, ...]:
    return tuple(
        Mt5TerminalCandidate(path=Path(path), source="Temporary allowlist", login=login, name=name, server=server, currency="USC")
        for path, login, name, server in FIXED_MONITORED_TERMINALS
        if Path(path).is_file()
    )


def _cache_file() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", "")) or Path.home() / ".gold_monitor"
    return root / "GOLDMonitor" / "mt5_terminal_cache.json"


def load_cached_candidates() -> tuple[Mt5TerminalCandidate, ...]:
    try:
        payload = json.loads(_cache_file().read_text(encoding="utf-8"))
        candidates = []
        for item in payload if isinstance(payload, list) else []:
            path = Path(str(item.get("path", "")))
            if path.is_file():
                candidates.append(Mt5TerminalCandidate(
                    path=path, source="Cache local", login=str(item.get("login", "")),
                    name=str(item.get("name", "")), server=str(item.get("server", "")),
                    currency=str(item.get("currency", "")), equity=item.get("equity"),
                ))
        return tuple(candidates)
    except (OSError, ValueError, TypeError):
        return ()


def _save_cache(candidates: list[Mt5TerminalCandidate]) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([
            {"path": str(item.path), "login": item.login, "name": item.name, "server": item.server,
             "currency": item.currency, "equity": item.equity}
            for item in candidates
        ], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _candidate_paths() -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(path: Path, source: str) -> None:
        key = str(path).casefold()
        if path.is_file() and key not in seen:
            seen.add(key)
            result.append((path, source))

    try:
        import psutil  # type: ignore
        for process in psutil.process_iter(["name", "exe"]):
            try:
                info = process.info or {}
                if str(info.get("name") or "").casefold() in {"terminal.exe", "terminal64.exe"} and info.get("exe"):
                    add(Path(str(info["exe"])), "Đang chạy")
            except (psutil.Error, OSError, ValueError):
                continue
    except ImportError:
        pass

    for cached in load_cached_candidates():
        add(cached.path, "Cache local")
    roots = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            for name in ("terminal64.exe", "terminal.exe"):
                for path in root.glob(f"**/{name}"):
                    add(path, "Cài đặt local")
        except OSError:
            continue
    return result


def scan_mt5_terminals(mt5: Any | None = None) -> tuple[Mt5TerminalCandidate, ...]:
    candidates: list[Mt5TerminalCandidate] = []
    for path, source in _candidate_paths():
        if mt5 is None:
            candidates.append(Mt5TerminalCandidate(path=path, source=source))
            continue
        try:
            if not mt5.initialize(path=str(path)):
                candidates.append(Mt5TerminalCandidate(path=path, source=source))
                continue
            info = mt5.account_info()
            candidates.append(Mt5TerminalCandidate(
                path=path, source=source, login=str(getattr(info, "login", "") or ""),
                name=str(getattr(info, "name", "") or ""), server=str(getattr(info, "server", "") or ""),
                currency=str(getattr(info, "currency", "") or ""), equity=float(getattr(info, "equity", 0.0) or 0.0),
            ))
        except Exception:
            candidates.append(Mt5TerminalCandidate(path=path, source=source))
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
    _save_cache(candidates)
    return tuple(candidates)


def open_remote_desktop(host: str, username: str = "") -> subprocess.Popen[bytes]:
    host = str(host or "").strip()
    if not host:
        raise ValueError("Cần nhập IP hoặc hostname VPS")
    command = ["mstsc.exe", f"/v:{host}"]
    if str(username).strip():
        command.append("/prompt")
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
