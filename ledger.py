"""Tiny SQLite "delivery ledger" that makes notifications safe to retry.

Your web backend may call the bot more than once for the same event (network
retry, double click, redelivery). Every message the bot sends is recorded
here under a key, so a repeated request never sends duplicates, while a
request that partially failed can be retried and only re-sends what did NOT
get delivered.

This stores delivery keys only - no course/student data. The web backend
remains the source of truth for everything else.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class DeliveryLedger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS delivered ("
                " key TEXT PRIMARY KEY,"
                " delivered_at TEXT NOT NULL)"
            )

    def _has(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM delivered WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _add(self, key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO delivered (key, delivered_at) VALUES (?, ?)", (key, now)
            )

    async def has(self, key: str) -> bool:
        return await asyncio.to_thread(self._has, key)

    async def add(self, key: str) -> None:
        await asyncio.to_thread(self._add, key)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
