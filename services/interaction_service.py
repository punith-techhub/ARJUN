"""Small per-user question broker for Telegram human-in-the-loop decisions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class InteractionTimeout(TimeoutError):
    """Raised when the user does not answer a blocking build question in time."""


@dataclass(slots=True)
class PendingQuestion:
    """One unanswered question and its private response future."""

    future: asyncio.Future[str]
    secret: bool


class InteractionBroker:
    """Route the next authorized Telegram message back to the waiting build."""

    def __init__(self, timeout_seconds: int = 900) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, PendingQuestion] = {}
        self._lock = asyncio.Lock()

    async def open(self, user_id: int, *, secret: bool) -> None:
        """Register a question before its Telegram prompt is sent."""
        async with self._lock:
            old = self._pending.pop(user_id, None)
            if old is not None and not old.future.done():
                old.future.cancel()
            self._pending[user_id] = PendingQuestion(
                future=asyncio.get_running_loop().create_future(),
                secret=secret,
            )

    async def wait(self, user_id: int) -> str:
        """Wait for the next answer, then remove the pending question."""
        async with self._lock:
            pending = self._pending.get(user_id)
        if pending is None:
            raise RuntimeError("No pending Arjun question exists")
        try:
            return await asyncio.wait_for(pending.future, self.timeout_seconds)
        except asyncio.TimeoutError as error:
            async with self._lock:
                self._pending.pop(user_id, None)
            raise InteractionTimeout("Timed out waiting for the Telegram answer") from error
        except (asyncio.CancelledError, Exception):
            async with self._lock:
                self._pending.pop(user_id, None)
            raise

    async def submit(self, user_id: int, response: str) -> bool:
        """Deliver a Telegram response and return whether it contained a secret."""
        async with self._lock:
            pending = self._pending.pop(user_id, None)
            if pending is None or pending.future.done():
                return False
            pending.future.set_result(response)
            return pending.secret

    async def cancel(self, user_id: int) -> bool:
        """Cancel any pending question for a user."""
        async with self._lock:
            pending = self._pending.pop(user_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()
                return True
            return False

    async def is_pending(self, user_id: int) -> bool:
        """Return whether the next message should be treated as an answer."""
        async with self._lock:
            pending = self._pending.get(user_id)
            return pending is not None and not pending.future.done()

