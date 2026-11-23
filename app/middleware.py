from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class PerChatLockMiddleware(BaseMiddleware):
    """Zachowuje kolejność aktualizacji w obrębie jednego prywatnego czatu."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = getattr(event, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            return await handler(event, data)
        async with self._locks[int(chat_id)]:
            return await handler(event, data)
