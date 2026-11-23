from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, MessageReactionUpdated, ReplyParameters

from .config import Settings
from .database import Database, format_code
from .keyboards import (
    BTN_CANCEL,
    BTN_JOIN,
    BTN_LEAVE,
    BTN_NEW,
    BTN_STATUS,
    cancel_keyboard,
    hide_keyboard,
    main_keyboard,
)
from .limits import SlidingWindowLimiter, WarningCooldown
from .middleware import PerChatLockMiddleware
from .transcription import TranscriptionJob, TranscriptionService


LOGGER = logging.getLogger(__name__)


class JoinStates(StatesGroup):
    waiting_for_code = State()


def build_router(
    db: Database,
    settings: Settings,
    transcription: TranscriptionService,
) -> Router:
    router = Router(name="bridge")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.edited_message.filter(F.chat.type == ChatType.PRIVATE)
    router.message_reaction.filter(F.chat.type == ChatType.PRIVATE)

    per_chat_lock = PerChatLockMiddleware()
    router.message.outer_middleware(per_chat_lock)
    router.edited_message.outer_middleware(per_chat_lock)
    router.message_reaction.outer_middleware(per_chat_lock)

    message_limiter = SlidingWindowLimiter(
        settings.message_rate_limit, settings.message_rate_window_seconds
    )
    join_limiter = SlidingWindowLimiter(
        settings.join_rate_limit, settings.join_rate_window_seconds
    )
    warning_cooldown = WarningCooldown(10)

    async def touch(message: Message) -> None:
        if message.from_user is None:
            return
        await db.upsert_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )

    async def markup_for(chat_id: int):
        return hide_keyboard() if await db.get_partner(chat_id) else main_keyboard()

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await touch(message)
        await state.clear()
        partner = await db.get_partner(message.chat.id)
        status = (
            "✅ Ваше прежнее подключение всё ещё активно. Кнопки скрыты, чтобы "
            "не занимать экран; команды доступны через меню Telegram."
            if partner
            else "Сейчас у вас нет активного подключения."
        )
        await message.answer(
            "<b>Приватный мост между двумя людьми</b>\n\n"
            "Один человек создаёт код, второй вводит его через кнопку "
            "«Подключиться по коду». После подключения сообщения копируются "
            "в чат второго человека.\n\n"
            f"{status}\n\n"
            "Бот не хранит тексты переписки, фотографии, файлы и записи. "
            "В базе остаются только пары пользователей, коды и техническое "
            "соответствие сообщений для ответов, правок, реакций и удаления через /del.",
            reply_markup=hide_keyboard() if partner else main_keyboard(),
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await touch(message)
        await message.answer(
            "<b>Команды</b>\n"
            "/new — создать одноразовый код\n"
            "/join КОД — подключиться по коду\n"
            "/status — проверить подключение\n"
            "/leave — отключиться\n"
            "/del — удалить у обоих сообщение, на которое вы ответили\n"
            "/cancel — отменить ввод кода\n\n"
            "Поддерживаются текст, фотографии, видео, документы, GIF, стикеры, "
            "голосовые, видеосообщения, геолокации, контакты, ответы, правки "
            "и обычные реакции Telegram.\n\n"
            "Обычное удаление пальцем Telegram не сообщает боту. Чтобы удалить "
            "сообщение у обоих, ответьте на него командой /del.",
            reply_markup=await markup_for(message.chat.id),
        )

    async def generate_code(message: Message) -> None:
        await touch(message)
        code, result = await db.create_invite_code(
            message.chat.id, settings.invite_ttl_minutes
        )
        if result == "already_connected":
            await message.answer(
                "У вас уже есть активное подключение. Сначала используйте /leave.",
                reply_markup=hide_keyboard(),
            )
            return
        if code is None:
            await message.answer("Не удалось создать код. Попробуйте ещё раз.")
            return
        await message.answer(
            "Передайте этот код второму человеку:\n\n"
            f"<code>{format_code(code)}</code>\n\n"
            f"Код действует {settings.invite_ttl_minutes} минут и используется один раз.",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("new"))
    async def new_command(message: Message) -> None:
        await generate_code(message)

    @router.message(F.text == BTN_NEW)
    async def new_button(message: Message) -> None:
        await generate_code(message)

    async def request_code(message: Message, state: FSMContext) -> None:
        await touch(message)
        if await db.get_partner(message.chat.id):
            await message.answer(
                "У вас уже есть активное подключение. Сначала используйте /leave.",
                reply_markup=hide_keyboard(),
            )
            return
        await state.set_state(JoinStates.waiting_for_code)
        await message.answer(
            "Введите код, полученный от второго человека, например "
            "<code>ABCD-2345</code>.",
            reply_markup=cancel_keyboard(),
        )

    @router.message(Command("join"))
    async def join_command(
        message: Message, state: FSMContext, command: CommandObject
    ) -> None:
        if command.args:
            await touch(message)
            await process_code(message, state, command.args)
        else:
            await request_code(message, state)

    @router.message(F.text == BTN_JOIN)
    async def join_button(message: Message, state: FSMContext) -> None:
        await request_code(message, state)

    @router.message(Command("cancel"))
    @router.message(F.text == BTN_CANCEL)
    async def cancel(message: Message, state: FSMContext) -> None:
        await touch(message)
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())

    @router.message(JoinStates.waiting_for_code, F.text)
    async def receive_code(message: Message, state: FSMContext) -> None:
        await touch(message)
        await process_code(message, state, message.text or "")

    async def process_code(message: Message, state: FSMContext, code: str) -> None:
        if not join_limiter.allow(message.chat.id):
            await message.answer(
                "Слишком много попыток ввода кода. Подождите несколько минут.",
                reply_markup=main_keyboard(),
            )
            await state.clear()
            return

        result = await db.join_invite(message.chat.id, code)
        messages = {
            "invalid_code": "Код неправильный или уже истёк.",
            "used_code": "Этот код уже был использован.",
            "expired_code": "Срок действия кода истёк.",
            "own_code": "Нельзя подключиться по собственному коду.",
            "joiner_connected": "У вас уже есть активное подключение.",
            "creator_connected": "Создатель кода уже подключился к другому человеку.",
        }
        if not result.ok or result.creator_id is None:
            await message.answer(
                messages.get(result.reason, "Не удалось создать подключение."),
                reply_markup=main_keyboard(),
            )
            return

        await state.clear()
        join_limiter.forget(message.chat.id)
        await message.answer(
            "✅ Подключение активно. Кнопки скрыты; просто пишите в этот чат.",
            reply_markup=hide_keyboard(),
        )
        try:
            await message.bot.send_message(
                result.creator_id,
                "✅ Второй человек использовал код. Подключение активно. "
                "Кнопки скрыты; просто пишите в этот чат.",
                reply_markup=hide_keyboard(),
            )
        except TelegramAPIError:
            LOGGER.warning("Не удалось уведомить создателя кода %s", result.creator_id)

    async def show_status(message: Message) -> None:
        await touch(message)
        partner = await db.get_partner(message.chat.id)
        await message.answer(
            "✅ Подключение активно."
            if partner
            else "У вас нет активного подключения.",
            reply_markup=hide_keyboard() if partner else main_keyboard(),
        )

    @router.message(Command("status"))
    async def status_command(message: Message) -> None:
        await show_status(message)

    @router.message(F.text == BTN_STATUS)
    async def status_button(message: Message) -> None:
        await show_status(message)

    async def leave(message: Message, state: FSMContext) -> None:
        await touch(message)
        await state.clear()
        partner = await db.leave_session(message.chat.id)
        if partner is None:
            await message.answer(
                "У вас нет активного подключения.", reply_markup=main_keyboard()
            )
            return
        await message.answer("Подключение завершено.", reply_markup=main_keyboard())
        try:
            await message.bot.send_message(
                partner,
                "Второй человек завершил подключение.",
                reply_markup=main_keyboard(),
            )
        except TelegramAPIError:
            LOGGER.warning("Не удалось уведомить пользователя %s об отключении", partner)

    @router.message(Command("leave"))
    async def leave_command(message: Message, state: FSMContext) -> None:
        await leave(message, state)

    @router.message(F.text == BTN_LEAVE)
    async def leave_button(message: Message, state: FSMContext) -> None:
        await leave(message, state)

    @router.message(Command("del"))
    @router.message(Command("delete"))
    async def delete_for_both(message: Message, bot: Bot) -> None:
        await touch(message)
        partner = await db.get_partner(message.chat.id)
        replied = message.reply_to_message
        if partner is None:
            await message.answer(
                "Нет активного подключения.", reply_markup=main_keyboard()
            )
            return
        if replied is None:
            await message.answer(
                "Ответьте командой /del на сообщение, которое нужно удалить у обоих.",
                reply_markup=hide_keyboard(),
            )
            return

        target = await db.get_mapped_target(
            message.chat.id, replied.message_id, expected_partner_id=partner
        )
        if target is None:
            await message.answer(
                "Для этого сообщения не найдено соответствие во втором чате.",
                reply_markup=hide_keyboard(),
            )
            return

        target_chat_id, target_message_id = target
        errors: list[str] = []
        try:
            await bot.delete_message(target_chat_id, target_message_id)
        except TelegramBadRequest as error:
            if "not found" not in str(error).lower():
                errors.append("не удалось удалить копию у второго человека")
                LOGGER.info("Удаление копии отклонено: %s", error)
        except TelegramAPIError:
            errors.append("ошибка Telegram при удалении копии")
            LOGGER.exception("Не удалось удалить сообщение в чате партнёра")

        try:
            await bot.delete_message(message.chat.id, replied.message_id)
        except TelegramBadRequest as error:
            if "not found" not in str(error).lower():
                errors.append("не удалось удалить сообщение в этом чате")
                LOGGER.info("Удаление исходного сообщения отклонено: %s", error)
        except TelegramAPIError:
            errors.append("ошибка Telegram при локальном удалении")
            LOGGER.exception("Не удалось удалить исходное сообщение")

        await db.delete_message_mapping(message.chat.id, replied.message_id)
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except TelegramAPIError:
            pass

        if errors:
            await bot.send_message(
                message.chat.id,
                "⚠️ " + "; ".join(errors) + ". Telegram разрешает удаление только ограниченное время.",
                reply_markup=hide_keyboard(),
            )

    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message) -> None:
        await touch(message)
        await message.answer(
            "Неизвестная команда. Используйте /help.",
            reply_markup=await markup_for(message.chat.id),
        )

    @router.edited_message()
    async def relay_edit(message: Message, bot: Bot) -> None:
        target = await db.get_edit_target(message.chat.id, message.message_id)
        if target is None:
            return
        target_chat_id, target_message_id = target
        try:
            if message.text is not None:
                await bot.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=target_message_id,
                    text=message.text,
                    entities=message.entities,
                    parse_mode=None,
                )
            elif message.caption is not None:
                await bot.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=target_message_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities,
                    parse_mode=None,
                )
        except TelegramAPIError:
            LOGGER.debug("Не удалось синхронизировать правку сообщения")

    @router.message_reaction()
    async def relay_reaction(event: MessageReactionUpdated, bot: Bot) -> None:
        target = await db.get_reaction_target(event.chat.id, event.message_id)
        if target is None:
            return
        target_chat_id, target_message_id = target
        reaction = [
            item
            for item in event.new_reaction
            if getattr(item, "type", None) != "paid"
        ][:1]
        try:
            await bot.set_message_reaction(
                chat_id=target_chat_id,
                message_id=target_message_id,
                reaction=reaction,
                is_big=False,
            )
        except TelegramBadRequest as error:
            LOGGER.info("Telegram не принял реакцию: %s", error)
        except TelegramAPIError:
            LOGGER.exception("Не удалось передать реакцию")

    @router.message()
    async def relay_message(message: Message, bot: Bot, state: FSMContext) -> None:
        await touch(message)
        current_state = await state.get_state()
        if current_state is not None:
            await message.answer(
                "Сначала введите код или нажмите «Отмена».",
                reply_markup=cancel_keyboard(),
            )
            return

        if not message_limiter.allow(message.chat.id):
            if warning_cooldown.should_warn(message.chat.id):
                await message.answer(
                    "⚠️ Вы отправляете сообщения слишком быстро. Подождите несколько секунд."
                )
            return

        partner = await db.get_partner(message.chat.id)
        if partner is None:
            await message.answer(
                "Сначала создайте подключение через «Создать код» или "
                "«Подключиться по коду».",
                reply_markup=main_keyboard(),
            )
            return

        reply_parameters: ReplyParameters | None = None
        reply_preview: str | None = None
        if message.reply_to_message is not None:
            reply_preview = _message_preview(message.reply_to_message)
            target_reply_id = await db.get_reply_target(
                current_chat_id=message.chat.id,
                replied_message_id=message.reply_to_message.message_id,
                expected_partner_id=partner,
            )
            if target_reply_id is not None:
                reply_parameters = ReplyParameters(message_id=target_reply_id)
            elif reply_preview:
                await _send_reply_fallback(bot, partner, reply_preview)

        try:
            copied = await _copy_with_one_retry(
                bot=bot,
                target_chat_id=partner,
                source_message=message,
                reply_parameters=reply_parameters,
            )
        except TelegramBadRequest as error:
            if reply_parameters is not None and _looks_like_reply_error(error):
                LOGGER.info("Нативный ответ не сработал, используем подпись: %s", error)
                if reply_preview:
                    await _send_reply_fallback(bot, partner, reply_preview)
                copied = await _copy_with_one_retry(
                    bot=bot,
                    target_chat_id=partner,
                    source_message=message,
                    reply_parameters=None,
                )
            else:
                LOGGER.info("Telegram отклонил тип сообщения: %s", error)
                await message.answer("Бот не может скопировать этот тип сообщения.")
                return
        except TelegramForbiddenError:
            await message.answer(
                "Сообщение не доставлено. Второй человек мог заблокировать бота "
                "или удалить чат. Подключение осталось сохранено."
            )
            return
        except (TelegramNetworkError, TelegramAPIError):
            LOGGER.exception("Ошибка передачи сообщения")
            await message.answer(
                "Временная ошибка Telegram. Отправьте сообщение ещё раз."
            )
            return

        await db.add_message_mapping(
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            target_chat_id=partner,
            target_message_id=copied.message_id,
        )

        if message.voice is not None:
            result = await transcription.enqueue(
                TranscriptionJob(
                    sender_id=message.chat.id,
                    recipient_id=partner,
                    file_id=message.voice.file_id,
                    kind="voice",
                    duration_seconds=message.voice.duration,
                    file_size=message.voice.file_size or 0,
                )
            )
            await _report_transcription_queue_result(message, result, settings)
        elif message.video_note is not None:
            result = await transcription.enqueue(
                TranscriptionJob(
                    sender_id=message.chat.id,
                    recipient_id=partner,
                    file_id=message.video_note.file_id,
                    kind="video_note",
                    duration_seconds=message.video_note.duration,
                    file_size=message.video_note.file_size or 0,
                )
            )
            await _report_transcription_queue_result(message, result, settings)

    return router


async def _copy_with_one_retry(
    bot: Bot,
    target_chat_id: int,
    source_message: Message,
    reply_parameters: ReplyParameters | None,
):
    try:
        return await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=source_message.chat.id,
            message_id=source_message.message_id,
            reply_parameters=reply_parameters,
        )
    except TelegramRetryAfter as error:
        if error.retry_after > 5:
            raise
        await asyncio.sleep(error.retry_after)
        return await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=source_message.chat.id,
            message_id=source_message.message_id,
            reply_parameters=reply_parameters,
        )


def _looks_like_reply_error(error: TelegramBadRequest) -> bool:
    value = str(error).lower()
    return "reply" in value or "replied" in value or "message not found" in value


def _message_preview(message: Message) -> str:
    raw = (message.text or message.caption or "").strip()
    if raw:
        compact = " ".join(raw.split())
        return compact[:180] + ("…" if len(compact) > 180 else "")
    if message.voice:
        return "голосовое сообщение"
    if message.video_note:
        return "видеосообщение"
    if message.photo:
        return "фотография"
    if message.video:
        return "видео"
    if message.document:
        return f"файл {message.document.file_name or ''}".strip()
    if message.sticker:
        return f"стикер {message.sticker.emoji or ''}".strip()
    if message.animation:
        return "GIF"
    if message.audio:
        return "аудиофайл"
    if message.contact:
        return "контакт"
    if message.location:
        return "геолокация"
    return "сообщение"


async def _send_reply_fallback(bot: Bot, chat_id: int, preview: str) -> None:
    safe = html.escape(preview)
    await bot.send_message(
        chat_id,
        f"↩️ <b>Ответ на сообщение:</b>\n<blockquote>{safe}</blockquote>",
    )


async def _report_transcription_queue_result(
    message: Message, result: str, settings: Settings
) -> None:
    if result in {"queued", "disabled"}:
        return
    if result == "too_long":
        await message.answer(
            "Запись передана, но не будет распознана: лимит составляет "
            f"{settings.transcription_max_duration_seconds} секунд."
        )
    elif result == "too_large":
        await message.answer(
            "Запись передана, но не будет распознана: файл превышает лимит "
            "для скачивания Telegram Bot API."
        )
    elif result == "user_busy":
        await message.answer(
            "Запись передана. У вас уже есть одно распознавание в очереди, "
            "поэтому эта запись не будет дополнительно нагружать сервер."
        )
    elif result == "queue_full":
        await message.answer(
            "Запись передана, но очередь распознавания заполнена. Попробуйте позже."
        )
