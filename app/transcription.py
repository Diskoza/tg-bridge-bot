from __future__ import annotations

import asyncio
import html
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from .config import Settings
from .database import Database
from .summary import SummaryService
from .text_cleanup import clean_transcript


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranscriptionJob:
    sender_id: int
    recipient_id: int
    file_id: str
    kind: str
    duration_seconds: int
    file_size: int


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    average_log_probability: float


class TranscriptionService:
    def __init__(self, bot: Bot, settings: Settings, db: Database) -> None:
        self.bot = bot
        self.settings = settings
        self.db = db
        self.summary = SummaryService(settings)
        self.queue: asyncio.Queue[TranscriptionJob] = asyncio.Queue(
            maxsize=settings.transcription_queue_size
        )
        self._workers: list[asyncio.Task[None]] = []
        self._pending_users: set[int] = set()
        self._pending_lock = asyncio.Lock()
        self._model: Any | None = None
        self._model_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self.settings.transcription_enabled:
            return
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"transcription-{index}")
            for index in range(self.settings.transcription_workers)
        ]
        LOGGER.info(
            "Запущено распознавание: model=%s language=%s workers=%d queue=%d mode=%s",
            self.settings.whisper_model,
            self.settings.whisper_language,
            self.settings.transcription_workers,
            self.settings.transcription_queue_size,
            self.settings.transcript_mode,
        )

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, job: TranscriptionJob) -> str:
        if not self.settings.transcription_enabled:
            return "disabled"
        if job.duration_seconds > self.settings.transcription_max_duration_seconds:
            return "too_long"
        if job.file_size > self.settings.transcription_max_file_mb * 1024 * 1024:
            return "too_large"

        async with self._pending_lock:
            if job.sender_id in self._pending_users:
                return "user_busy"
            if self.queue.full():
                return "queue_full"
            self._pending_users.add(job.sender_id)
            self.queue.put_nowait(job)
        return "queued"

    async def _worker(self, worker_number: int) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Ошибка распознавания worker=%d", worker_number)
                await self._safe_send(
                    job.sender_id,
                    "⚠️ Не удалось подготовить текст этого сообщения. "
                    "Само голосовое уже было передано.",
                )
            finally:
                async with self._pending_lock:
                    self._pending_users.discard(job.sender_id)
                self.queue.task_done()

    async def _process(self, job: TranscriptionJob) -> None:
        suffix = ".mp4" if job.kind == "video_note" else ".ogg"
        temp_path = self.settings.temp_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            await self.bot.download(job.file_id, destination=temp_path, timeout=90)
            model = await self._get_model()
            decoded = await asyncio.to_thread(
                self._transcribe_blocking,
                model,
                temp_path,
                job.duration_seconds,
            )
            raw_text = decoded.text
            LOGGER.info(
                "Распознавание завершено: duration=%ss avg_logprob=%.3f",
                job.duration_seconds,
                decoded.average_log_probability,
            )

            if self.settings.transcript_mode == "raw":
                final_text = raw_text.strip()
            elif self.settings.transcript_mode == "clean":
                final_text = clean_transcript(raw_text)
            else:
                final_text = await self.summary.prepare(raw_text)

            if not final_text:
                await self._safe_send(
                    job.sender_id,
                    "📝 В записи не удалось распознать разборчивую русскую речь.",
                )
                return

            escaped = html.escape(final_text)
            if job.kind == "video_note":
                own_object = "видеосообщения"
                received_object = "полученного видеосообщения"
            else:
                own_object = "голосового сообщения"
                received_object = "полученного голосового сообщения"

            if self.settings.transcript_mode == "summary":
                own_title = f"📝 <b>Суть вашего {own_object}</b>"
                received_title = f"📝 <b>Суть {received_object}</b>"
            else:
                own_title = f"📝 <b>Текст вашего {own_object}</b>"
                received_title = f"📝 <b>Текст {received_object}</b>"

            own_message = await self._safe_send(
                job.sender_id, f"{own_title}\n{escaped}"
            )
            received_message = await self._safe_send(
                job.recipient_id, f"{received_title}\n{escaped}"
            )
            if own_message is not None and received_message is not None:
                await self.db.add_message_mapping(
                    source_chat_id=job.sender_id,
                    source_message_id=own_message.message_id,
                    target_chat_id=job.recipient_id,
                    target_message_id=received_message.message_id,
                )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Не удалось удалить временный файл: %s", temp_path)

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                LOGGER.info("Загрузка модели Whisper: %s", self.settings.whisper_model)
                self._model = await asyncio.to_thread(self._load_model_blocking)
        return self._model

    def _load_model_blocking(self) -> Any:
        from faster_whisper import WhisperModel

        requested = self.settings.whisper_device
        if requested not in {"auto", "cpu", "cuda"}:
            raise ValueError("WHISPER_DEVICE: допустимые значения auto, cpu или cuda")

        if requested in {"auto", "cuda"}:
            try:
                LOGGER.info("Пробуем загрузить Whisper на NVIDIA GPU")
                return WhisperModel(
                    self.settings.whisper_model,
                    device="cuda",
                    compute_type="float16",
                    cpu_threads=self.settings.whisper_cpu_threads,
                    num_workers=1,
                    download_root=str(self.settings.model_dir),
                )
            except Exception:
                if requested == "cuda":
                    LOGGER.exception("Whisper не запустился на CUDA")
                    raise
                LOGGER.warning(
                    "CUDA недоступна или не настроена; используем CPU int8",
                    exc_info=True,
                )

        return WhisperModel(
            self.settings.whisper_model,
            device="cpu",
            compute_type=self.settings.whisper_compute_type,
            cpu_threads=self.settings.whisper_cpu_threads,
            num_workers=1,
            download_root=str(self.settings.model_dir),
        )

    def _transcribe_blocking(
        self,
        model: Any,
        path: Path,
        duration_seconds: int,
    ) -> DecodedText:
        first = self._transcribe_once(
            model,
            path,
            duration_seconds,
            beam_size=self.settings.whisper_beam_size,
            use_vad=duration_seconds > 7,
            condition_on_previous_text=duration_seconds >= 15,
        )

        if duration_seconds >= 20 and first.average_log_probability < -1.05:
            LOGGER.info(
                "Низкая уверенность Whisper (%.3f), запускаем точный повторный проход",
                first.average_log_probability,
            )
            second = self._transcribe_once(
                model,
                path,
                duration_seconds,
                beam_size=max(8, self.settings.whisper_beam_size),
                use_vad=True,
                condition_on_previous_text=True,
            )
            if second.average_log_probability > first.average_log_probability:
                return second
        return first

    def _transcribe_once(
        self,
        model: Any,
        path: Path,
        duration_seconds: int,
        *,
        beam_size: int,
        use_vad: bool,
        condition_on_previous_text: bool,
    ) -> DecodedText:
        kwargs: dict[str, Any] = {
            "language": self.settings.whisper_language,
            "task": "transcribe",
            "beam_size": beam_size,
            "best_of": beam_size,
            "temperature": 0.0,
            "vad_filter": use_vad,
            "condition_on_previous_text": condition_on_previous_text,
            "word_timestamps": False,
            "initial_prompt": (
                "Разговор на русском языке. Точно записывай имена, числа, суммы, "
                "валюты, планы, отрицания и причинно-следственные связи. "
                "Используй русскую пунктуацию."
            ),
            "hotwords": (
                "рублей рубля злотых злотый евро долларов гривен работа "
                "оборудование норма выезд сегодня завтра сумма итого"
            ),
        }
        if use_vad:
            kwargs["vad_parameters"] = {
                "threshold": 0.30,
                "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 300,
            }

        segments_iter, _ = model.transcribe(str(path), **kwargs)
        segments = [segment for segment in segments_iter if segment.text.strip()]
        if not segments and use_vad:
            return self._transcribe_once(
                model,
                path,
                duration_seconds,
                beam_size=beam_size,
                use_vad=False,
                condition_on_previous_text=condition_on_previous_text,
            )

        text = " ".join(segment.text.strip() for segment in segments).strip()
        total_weight = 0.0
        weighted_logprob = 0.0
        for segment in segments:
            weight = max(0.1, float(segment.end) - float(segment.start))
            total_weight += weight
            weighted_logprob += float(segment.avg_logprob) * weight
        average = weighted_logprob / total_weight if total_weight else -99.0
        return DecodedText(text=text, average_log_probability=average)

    async def _safe_send(self, chat_id: int, text: str) -> Message | None:
        try:
            return await self.bot.send_message(chat_id, text)
        except TelegramAPIError:
            LOGGER.warning(
                "Не удалось отправить результат распознавания пользователю %s",
                chat_id,
            )
            return None
