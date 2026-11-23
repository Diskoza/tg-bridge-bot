from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
data = root / "data" / "bridge.sqlite3"
backups = root / "backups"
backups.mkdir(parents=True, exist_ok=True)
if not data.exists():
    raise SystemExit("База ещё не создана")
target = backups / f"bridge_{datetime.now():%Y-%m-%d_%H-%M-%S}.sqlite3"
with sqlite3.connect(data) as source, sqlite3.connect(target) as dest:
    source.backup(dest)
for old in sorted(backups.glob("bridge_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[14:]:
    old.unlink(missing_ok=True)
print(target)
