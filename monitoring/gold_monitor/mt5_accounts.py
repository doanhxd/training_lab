from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parents[2] / "mt5_terminals.json"


@dataclass(frozen=True)
class Mt5TerminalCandidate:
    path: Path
    source: str
    login: str = ""
    name: str = ""
    server: str = ""
    currency: str = ""
    equity: float | None = None


AUTO_OPEN_ALLOWLIST_COUNT = 0  # legacy compatibility only; config is authoritative.


def _configured_terminals() -> tuple[tuple[tuple[str, str, str, str], ...], int]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        rows = payload.get("terminals", []) if isinstance(payload, dict) else []
        terminals = tuple(
            (
                str(item.get("path", "")), str(item.get("login", "")),
                str(item.get("name", "")), str(item.get("server", "")),
            )
            for item in rows if isinstance(item, dict) and str(item.get("path", "")).strip()
        )
        count = int(payload.get("auto_open_count", 0)) if isinstance(payload, dict) else 0
        return terminals, max(0, count)
    except (OSError, ValueError, TypeError):
        return (), 0


def configured_auto_open_count() -> int:
    _terminals, count = _configured_terminals()
    return count


def load_fixed_candidates() -> tuple[Mt5TerminalCandidate, ...]:
    configured, _auto_open_count = _configured_terminals()
    return tuple(
        Mt5TerminalCandidate(path=Path(path), source="Temporary allowlist", login=login, name=name, server=server, currency="USC")
        for path, login, name, server in configured
    )


def open_local_terminal(path: Path | str) -> None:
    terminal = Path(path)
    if not terminal.is_file():
        raise FileNotFoundError(f"Không tìm thấy MT5 terminal: {terminal}")
    subprocess.Popen([str(terminal)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def terminal_is_running(path: Path | str) -> bool:
    target = str(Path(path)).casefold()
    try:
        import psutil  # type: ignore
        for process in psutil.process_iter(["name", "exe"]):
            try:
                info = process.info or {}
                executable = str(info.get("exe") or "").casefold()
                process_name = str(info.get("name") or "").casefold()
                if executable and (executable == target or Path(executable).name == Path(target).name and Path(executable).parent == Path(target).parent):
                    return True
                if process_name in {"terminal.exe", "terminal64.exe"} and executable:
                    if Path(executable).name == Path(target).name and Path(executable).parent.name.casefold() == Path(target).parent.name.casefold():
                        return True
            except (psutil.Error, OSError, ValueError):
                continue
    except ImportError:
        return False
    return False


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
