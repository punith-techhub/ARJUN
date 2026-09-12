"""Encrypted storage for user-supplied application environment variables."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    """Raised when secrets cannot be safely persisted or decrypted."""


class SecretStore:
    """Keep application secrets encrypted at rest and outside model/GitHub context."""

    _key_pattern = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")

    def __init__(self, database_path: str, master_key: str, scope: str = "__global__") -> None:
        cleaned = (master_key or "").strip()
        if not cleaned or cleaned in {"replace_with_a_generated_fernet_key", '""', "''"}:
            self._cipher: Fernet | None = None
        else:
            try:
                self._cipher = Fernet(cleaned.encode("ascii"))
            except Exception:
                # Deterministically derive 32-byte url-safe base64 key from any passphrase
                derived = base64.urlsafe_b64encode(hashlib.sha256(cleaned.encode("utf-8")).digest())
                self._cipher = Fernet(derived)
        self.database_path = database_path
        self.scope = scope
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS encrypted_secrets_v2 (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    ciphertext BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, key)
                )
                """
            )
            # Keep a compatibility table so old installations can be read safely.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS encrypted_secrets (
                    key TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @classmethod
    def validate_key(cls, key: str) -> str:
        """Normalize an environment key and reject shell/code injection."""
        normalized = key.strip().upper()
        if not cls._key_pattern.fullmatch(normalized):
            raise SecretStoreError(f"Invalid environment variable name: {key}")
        return normalized

    def _require_cipher(self) -> Fernet:
        if self._cipher is None:
            raise SecretStoreError(
                "Set ARJUN_SECRET_KEY once before accepting credentials through Telegram"
            )
        return self._cipher

    async def has(self, key: str) -> bool:
        """Return whether the key exists in worker environment or encrypted storage."""
        return await asyncio.to_thread(self._has, self.validate_key(key))

    def _has(self, key: str) -> bool:
        if os.getenv(key):
            return True
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM encrypted_secrets_v2 WHERE scope = ? AND key = ?",
                (self.scope, key),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT 1 FROM encrypted_secrets WHERE key = ?", (key,)
                ).fetchone()
        return row is not None

    async def get(self, key: str) -> str | None:
        """Read a key, preferring a runtime environment value over encrypted storage."""
        return await asyncio.to_thread(self._get, self.validate_key(key))

    def _get(self, key: str) -> str | None:
        runtime_value = os.getenv(key)
        if runtime_value:
            return runtime_value
        with self._connect() as connection:
            row = connection.execute(
                "SELECT ciphertext FROM encrypted_secrets_v2 WHERE scope = ? AND key = ?",
                (self.scope, key),
            ).fetchone()
            if row is None:
                # Backward-compatible read of the pre-project-scoped store.
                row = connection.execute(
                    "SELECT ciphertext FROM encrypted_secrets WHERE key = ?", (key,)
                ).fetchone()
        if row is None:
            return None
        try:
            return self._require_cipher().decrypt(bytes(row[0])).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise SecretStoreError(
                f"Stored credential {key} cannot be decrypted; ARJUN_SECRET_KEY may have changed"
            ) from error

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        """Return only requested values; never expose the complete secret store."""
        values: dict[str, str] = {}
        for key in keys:
            value = await self.get(key)
            if value:
                values[self.validate_key(key)] = value
        return values

    async def save_many(self, values: Mapping[str, str]) -> tuple[str, ...]:
        """Encrypt and persist a validated allow-list of user-supplied values."""
        return await asyncio.to_thread(self._save_many, dict(values))

    def _save_many(self, values: dict[str, str]) -> tuple[str, ...]:
        cipher = self._require_cipher()
        saved: list[str] = []
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            for raw_key, value in values.items():
                key = self.validate_key(raw_key)
                if not value.strip():
                    raise SecretStoreError(f"Credential {key} is empty")
                ciphertext = cipher.encrypt(value.strip().encode("utf-8"))
                connection.execute(
                    """
                    INSERT INTO encrypted_secrets_v2(scope, key, ciphertext, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope, key) DO UPDATE SET
                        ciphertext = excluded.ciphertext,
                        updated_at = excluded.updated_at
                    """,
                    (self.scope, key, ciphertext, now),
                )
                saved.append(key)
        return tuple(saved)

    @classmethod
    def parse_assignments(cls, response: str, expected_keys: set[str]) -> dict[str, str]:
        """Parse only requested KEY=value lines without logging their values."""
        values: dict[str, str] = {}
        for raw_line in response.replace("```", "").splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith("json"):
                continue
            separator = "=" if "=" in line else ":" if ":" in line else ""
            if not separator:
                continue
            raw_key, raw_value = line.split(separator, 1)
            key = cls.validate_key(raw_key)
            if key not in expected_keys:
                continue
            value = raw_value.strip().strip("\"'")
            if value:
                values[key] = value
        return values

    async def close(self) -> None:
        """Connections are scoped to individual operations."""
