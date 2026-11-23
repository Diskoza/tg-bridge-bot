from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


LOGGER = logging.getLogger(__name__)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True, slots=True)
class JoinResult:
    ok: bool
    reason: str
    creator_id: int | None = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не открыта")
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=10)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                used_by INTEGER,
                FOREIGN KEY (creator_id) REFERENCES users(telegram_id),
                FOREIGN KEY (used_by) REFERENCES users(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_invites_creator
                ON invite_codes(creator_id, used_at, expires_at);

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at INTEGER NOT NULL,
                ended_at INTEGER,
                CHECK(user1_id <> user2_id),
                FOREIGN KEY (user1_id) REFERENCES users(telegram_id),
                FOREIGN KEY (user2_id) REFERENCES users(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user1_active
                ON sessions(user1_id, active);
            CREATE INDEX IF NOT EXISTS idx_sessions_user2_active
                ON sessions(user2_id, active);

            CREATE TABLE IF NOT EXISTS message_map (
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                target_chat_id INTEGER NOT NULL,
                target_message_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(source_chat_id, source_message_id, target_chat_id)
            );

            CREATE INDEX IF NOT EXISTS idx_message_map_target
                ON message_map(target_chat_id, target_message_id);
            CREATE INDEX IF NOT EXISTS idx_message_map_created
                ON message_map(created_at);
            """
        )
        await self._conn.commit()

        async with self._conn.execute("PRAGMA quick_check") as cursor:
            row = await cursor.fetchone()
            if not row or row[0] != "ok":
                raise RuntimeError(f"SQLite quick_check не пройден: {row[0] if row else 'нет результата'}")
        LOGGER.info("База SQLite готова: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_user(self, telegram_id: int, username: str | None, display_name: str) -> None:
        now = int(time.time())
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO users(telegram_id, username, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, username, display_name, now, now),
            )
            await self.conn.commit()

    async def get_partner(self, telegram_id: int) -> int | None:
        async with self.conn.execute(
            """
            SELECT CASE WHEN user1_id = ? THEN user2_id ELSE user1_id END AS partner_id
            FROM sessions
            WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id, telegram_id, telegram_id),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["partner_id"]) if row else None

    async def create_invite_code(self, creator_id: int, ttl_minutes: int) -> tuple[str | None, str]:
        now = int(time.time())
        expires_at = now + ttl_minutes * 60
        async with self._write_lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                active = await self._has_active_session_locked(creator_id)
                if active:
                    await self.conn.rollback()
                    return None, "already_connected"

                await self.conn.execute(
                    "DELETE FROM invite_codes WHERE creator_id = ? AND used_at IS NULL",
                    (creator_id,),
                )
                for _ in range(20):
                    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
                    try:
                        await self.conn.execute(
                            """
                            INSERT INTO invite_codes(code, creator_id, created_at, expires_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (code, creator_id, now, expires_at),
                        )
                        await self.conn.commit()
                        return code, "ok"
                    except aiosqlite.IntegrityError:
                        continue
                await self.conn.rollback()
                return None, "generation_failed"
            except Exception:
                await self.conn.rollback()
                raise

    async def join_invite(self, user_id: int, code: str) -> JoinResult:
        now = int(time.time())
        normalized = normalize_code(code)
        async with self._write_lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                await self.conn.execute(
                    "DELETE FROM invite_codes WHERE used_at IS NULL AND expires_at <= ?",
                    (now,),
                )
                async with self.conn.execute(
                    """
                    SELECT creator_id, expires_at, used_at
                    FROM invite_codes
                    WHERE code = ?
                    LIMIT 1
                    """,
                    (normalized,),
                ) as cursor:
                    invite = await cursor.fetchone()

                if invite is None:
                    await self.conn.rollback()
                    return JoinResult(False, "invalid_code")
                if invite["used_at"] is not None:
                    await self.conn.rollback()
                    return JoinResult(False, "used_code")
                if int(invite["expires_at"]) <= now:
                    await self.conn.rollback()
                    return JoinResult(False, "expired_code")

                creator_id = int(invite["creator_id"])
                if creator_id == user_id:
                    await self.conn.rollback()
                    return JoinResult(False, "own_code")
                if await self._has_active_session_locked(user_id):
                    await self.conn.rollback()
                    return JoinResult(False, "joiner_connected")
                if await self._has_active_session_locked(creator_id):
                    await self.conn.rollback()
                    return JoinResult(False, "creator_connected")

                await self.conn.execute(
                    """
                    INSERT INTO sessions(user1_id, user2_id, active, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (creator_id, user_id, now),
                )
                await self.conn.execute(
                    """
                    UPDATE invite_codes
                    SET used_at = ?, used_by = ?
                    WHERE code = ? AND used_at IS NULL
                    """,
                    (now, user_id, normalized),
                )
                await self.conn.commit()
                return JoinResult(True, "ok", creator_id)
            except Exception:
                await self.conn.rollback()
                raise

    async def leave_session(self, user_id: int) -> int | None:
        now = int(time.time())
        async with self._write_lock:
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                async with self.conn.execute(
                    """
                    SELECT id,
                           CASE WHEN user1_id = ? THEN user2_id ELSE user1_id END AS partner_id
                    FROM sessions
                    WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (user_id, user_id, user_id),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    await self.conn.rollback()
                    return None
                await self.conn.execute(
                    "UPDATE sessions SET active = 0, ended_at = ? WHERE id = ?",
                    (now, int(row["id"])),
                )
                await self.conn.commit()
                return int(row["partner_id"])
            except Exception:
                await self.conn.rollback()
                raise

    async def add_message_mapping(
        self,
        source_chat_id: int,
        source_message_id: int,
        target_chat_id: int,
        target_message_id: int,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO message_map(
                    source_chat_id, source_message_id, target_chat_id,
                    target_message_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_chat_id,
                    source_message_id,
                    target_chat_id,
                    target_message_id,
                    int(time.time()),
                ),
            )
            await self.conn.commit()

    async def get_reply_target(
        self,
        current_chat_id: int,
        replied_message_id: int,
        expected_partner_id: int,
    ) -> int | None:
        """Return the corresponding message id in the partner chat.

        A user can reply either to a received bot copy or to their own original
        message. Both directions are supported.
        """
        async with self.conn.execute(
            """
            SELECT target_message_id AS partner_message_id,
                   0 AS direction
            FROM message_map
            WHERE source_chat_id = ?
              AND source_message_id = ?
              AND target_chat_id = ?

            UNION ALL

            SELECT source_message_id AS partner_message_id,
                   1 AS direction
            FROM message_map
            WHERE target_chat_id = ?
              AND target_message_id = ?
              AND source_chat_id = ?

            ORDER BY direction DESC
            LIMIT 1
            """,
            (
                current_chat_id,
                replied_message_id,
                expected_partner_id,
                current_chat_id,
                replied_message_id,
                expected_partner_id,
            ),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["partner_message_id"]) if row else None

    async def get_mapped_target(
        self,
        current_chat_id: int,
        current_message_id: int,
        expected_partner_id: int | None = None,
    ) -> tuple[int, int] | None:
        params: tuple[int, ...]
        partner_source_filter = ""
        partner_target_filter = ""
        if expected_partner_id is None:
            params = (
                current_chat_id,
                current_message_id,
                current_chat_id,
                current_message_id,
            )
        else:
            partner_source_filter = " AND target_chat_id = ?"
            partner_target_filter = " AND source_chat_id = ?"
            params = (
                current_chat_id,
                current_message_id,
                expected_partner_id,
                current_chat_id,
                current_message_id,
                expected_partner_id,
            )

        query = f"""
            SELECT target_chat_id AS other_chat_id,
                   target_message_id AS other_message_id,
                   0 AS direction
            FROM message_map
            WHERE source_chat_id = ? AND source_message_id = ?
            {partner_source_filter}

            UNION ALL

            SELECT source_chat_id AS other_chat_id,
                   source_message_id AS other_message_id,
                   1 AS direction
            FROM message_map
            WHERE target_chat_id = ? AND target_message_id = ?
            {partner_target_filter}

            ORDER BY direction DESC
            LIMIT 1
        """
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return int(row["other_chat_id"]), int(row["other_message_id"])

    async def delete_message_mapping(
        self, current_chat_id: int, current_message_id: int
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                DELETE FROM message_map
                WHERE (source_chat_id = ? AND source_message_id = ?)
                   OR (target_chat_id = ? AND target_message_id = ?)
                """,
                (
                    current_chat_id,
                    current_message_id,
                    current_chat_id,
                    current_message_id,
                ),
            )
            await self.conn.commit()

    async def get_edit_target(
        self, source_chat_id: int, source_message_id: int
    ) -> tuple[int, int] | None:
        async with self.conn.execute(
            """
            SELECT target_chat_id, target_message_id
            FROM message_map
            WHERE source_chat_id = ? AND source_message_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (source_chat_id, source_message_id),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return int(row["target_chat_id"]), int(row["target_message_id"])

    async def get_reaction_target(
        self, current_chat_id: int, current_message_id: int
    ) -> tuple[int, int] | None:
        return await self.get_mapped_target(current_chat_id, current_message_id)

    async def cleanup(self) -> None:
        now = int(time.time())
        message_cutoff = now - 180 * 24 * 60 * 60
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM invite_codes WHERE expires_at < ? OR used_at IS NOT NULL AND used_at < ?",
                (now - 24 * 60 * 60, now - 7 * 24 * 60 * 60),
            )
            await self.conn.execute(
                "DELETE FROM message_map WHERE created_at < ?",
                (message_cutoff,),
            )
            await self.conn.commit()
            await self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    async def _has_active_session_locked(self, user_id: int) -> bool:
        async with self.conn.execute(
            """
            SELECT 1 FROM sessions
            WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
            LIMIT 1
            """,
            (user_id, user_id),
        ) as cursor:
            return await cursor.fetchone() is not None


def normalize_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def format_code(value: str) -> str:
    normalized = normalize_code(value)
    return f"{normalized[:4]}-{normalized[4:8]}"
