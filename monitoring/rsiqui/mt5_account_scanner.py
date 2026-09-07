from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any
import json


@dataclass(frozen=True)
class Mt5TerminalCandidate:
    path: Path
    source: str
    login: str = ""
    name: str = ""
    server: str = ""
    currency: str = ""
    equity: float | None = None
    status: str = "Chưa đọc account"

    @property
    def display_name(self) -> str:
        identity = " / ".join(part for part in (self.login, self.server) if part)
        return f"{identity} — {self.path}" if identity else str(self.path)


def _cache_file() -> Path:
    """Return a local, credential-free cache for discovered MT5 terminals."""
    root = Path(os.environ.get("LOCALAPPDATA", ""))
    if not str(root):
        root = Path.home() / ".gold_trader"
    return root / "GOLDTrader" / "mt5_terminal_cache.json"


def load_cached_candidates() -> tuple[Mt5TerminalCandidate, ...]:
    """Load the last terminal/account display; never stores passwords or tokens."""
    try:
        payload = json.loads(_cache_file().read_text(encoding="utf-8"))
        result = []
        for item in payload if isinstance(payload, list) else []:
            path = Path(str(item.get("path", "")))
            if path.is_file():
                result.append(Mt5TerminalCandidate(
                    path=path,
                    source="Cache local",
                    login=str(item.get("login", "")),
                    name=str(item.get("name", "")),
                    server=str(item.get("server", "")),
                    currency=str(item.get("currency", "")),
                    equity=item.get("equity"),
                    status="Cache gần nhất",
                ))
        return tuple(result)
    except (OSError, ValueError, TypeError):
        return ()


def _save_cached_candidates(candidates: list[Mt5TerminalCandidate]) -> None:
    """Persist paths and harmless display metadata only."""
    cache = _cache_file()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([
            {"path": str(item.path), "login": item.login, "name": item.name, "server": item.server,
             "currency": item.currency, "equity": item.equity}
            for item in candidates
        ], ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _candidate_paths() -> list[tuple[Path, str]]:
    paths: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(path: Path, source: str) -> None:
        key = str(path).casefold()
        if path.is_file() and key not in seen:
            seen.add(key)
            paths.append((path, source))

    try:
        import psutil  # type: ignore
        for process in psutil.process_iter(["name", "exe"]):
            try:
                name = str((process.info or {}).get("name") or "").casefold()
                exe = (process.info or {}).get("exe")
                if exe and name in {"terminal64.exe", "terminal.exe"}:
                    add(Path(exe), "Đang chạy")
            except (psutil.Error, OSError, ValueError):
                continue
    except ImportError:
        pass

    # Cached paths avoid an expensive recursive scan of Program Files on every click.
    for cached in load_cached_candidates():
        add(cached.path, "Cache local")

    roots = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        Path(os.environ.get("APPDATA", "")),
    ]
    for root in roots:
        if not root.exists() or any(source == "Cache local" for _, source in paths):
            continue
        try:
            for path in root.glob("**/terminal64.exe"):
                add(path, "Cài đặt local")
            for path in root.glob("**/terminal.exe"):
                add(path, "Cài đặt local")
        except OSError:
            continue
    return paths


def scan_mt5_terminals(mt5: Any | None = None) -> tuple[Mt5TerminalCandidate, ...]:
    candidates: list[Mt5TerminalCandidate] = []
    for path, source in _candidate_paths():
        candidate = Mt5TerminalCandidate(path=path, source=source)
        if mt5 is None:
            candidates.append(candidate)
            continue
        try:
            initialized = bool(mt5.initialize(path=str(path)))
            if not initialized:
                candidates.append(Mt5TerminalCandidate(path=path, source=source, status="Không kết nối được"))
                continue
            info = mt5.account_info()
            if info is None:
                candidates.append(Mt5TerminalCandidate(path=path, source=source, status="Không có account"))
            else:
                candidates.append(Mt5TerminalCandidate(
                    path=path,
                    source=source,
                    login=str(getattr(info, "login", "") or ""),
                    name=str(getattr(info, "name", "") or ""),
                    server=str(getattr(info, "server", "") or ""),
                    currency=str(getattr(info, "currency", "") or ""),
                    equity=float(getattr(info, "equity", 0.0) or 0.0),
                    status="Đã đọc read-only",
                ))
        except Exception as exc:
            candidates.append(Mt5TerminalCandidate(path=path, source=source, status=f"Lỗi: {exc}"))
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
    _save_cached_candidates(candidates)
    return tuple(candidates)


def open_remote_desktop(host: str, username: str = "") -> subprocess.Popen[bytes]:
    host = str(host or "").strip()
    if not host:
        raise ValueError("Cần nhập IP hoặc hostname VPS")
    command = ["mstsc.exe", f"/v:{host}"]
    # mstsc intentionally receives no password. Windows handles the credential prompt.
    if username.strip():
        command.append(f"/prompt")
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
