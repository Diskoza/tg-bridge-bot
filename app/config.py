from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} должно быть не меньше {minimum}")
    return value


def _as_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} должно быть не меньше {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    data_dir: Path
    temp_dir: Path
    model_dir: Path
    database_path: Path

    invite_ttl_minutes: int
    update_concurrency_limit: int
    message_rate_limit: int
    message_rate_window_seconds: int
    join_rate_limit: int
    join_rate_window_seconds: int

    transcription_enabled: bool
    whisper_model: str
    whisper_device: str
    whisper_language: str
    whisper_compute_type: str
    whisper_cpu_threads: int
    whisper_beam_size: int
    transcription_workers: int
    transcription_queue_size: int
    transcription_max_duration_seconds: int
    transcription_max_file_mb: int
    transcript_mode: str

    summary_backend: str
    summary_api_url: str
    summary_model: str
    summary_timeout_seconds: int
    summary_min_words: int
    summary_target_words: int
    summary_max_words: int
    summary_temperature: float

    @classmethod
    def load(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or ":" not in token:
            raise RuntimeError("В файле .env отсутствует корректный BOT_TOKEN")

        data_dir = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).expanduser().resolve()
        temp_dir = Path(os.getenv("TEMP_DIR", BASE_DIR / "tmp")).expanduser().resolve()
        model_dir = Path(os.getenv("MODEL_DIR", BASE_DIR / "models")).expanduser().resolve()
        for directory in (data_dir, temp_dir, model_dir):
            directory.mkdir(parents=True, exist_ok=True)

        os.environ.setdefault("HF_HOME", str(model_dir))
        os.environ.setdefault("OMP_NUM_THREADS", os.getenv("WHISPER_CPU_THREADS", "2"))
        os.environ.setdefault("MKL_NUM_THREADS", os.getenv("WHISPER_CPU_THREADS", "2"))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        transcript_mode = os.getenv("TRANSCRIPT_MODE", "summary").strip().lower()
        if transcript_mode not in {"raw", "clean", "summary"}:
            raise ValueError("TRANSCRIPT_MODE: допустимые значения raw, clean или summary")

        summary_backend = os.getenv("SUMMARY_BACKEND", "extractive").strip().lower()
        if summary_backend not in {"extractive", "local_llm"}:
            raise ValueError("SUMMARY_BACKEND: допустимые значения extractive или local_llm")

        return cls(
            bot_token=token,
            data_dir=data_dir,
            temp_dir=temp_dir,
            model_dir=model_dir,
            database_path=data_dir / "bridge.sqlite3",
            invite_ttl_minutes=_as_int("INVITE_TTL_MINUTES", 30, 1),
            update_concurrency_limit=_as_int("UPDATE_CONCURRENCY_LIMIT", 16, 1),
            message_rate_limit=_as_int("MESSAGE_RATE_LIMIT", 25, 1),
            message_rate_window_seconds=_as_int("MESSAGE_RATE_WINDOW_SECONDS", 10, 1),
            join_rate_limit=_as_int("JOIN_RATE_LIMIT", 6, 1),
            join_rate_window_seconds=_as_int("JOIN_RATE_WINDOW_SECONDS", 600, 1),
            transcription_enabled=_as_bool("TRANSCRIPTION_ENABLED", True),
            whisper_model=os.getenv("WHISPER_MODEL", "small").strip(),
            whisper_device=os.getenv("WHISPER_DEVICE", "auto").strip().lower(),
            whisper_language=os.getenv("WHISPER_LANGUAGE", "ru").strip() or "ru",
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip(),
            whisper_cpu_threads=_as_int("WHISPER_CPU_THREADS", 2, 1),
            whisper_beam_size=_as_int("WHISPER_BEAM_SIZE", 5, 1),
            transcription_workers=_as_int("TRANSCRIPTION_WORKERS", 1, 1),
            transcription_queue_size=_as_int("TRANSCRIPTION_QUEUE_SIZE", 4, 1),
            transcription_max_duration_seconds=_as_int(
                "TRANSCRIPTION_MAX_DURATION_SECONDS", 120, 1
            ),
            transcription_max_file_mb=_as_int("TRANSCRIPTION_MAX_FILE_MB", 18, 1),
            transcript_mode=transcript_mode,
            summary_backend=summary_backend,
            summary_api_url=os.getenv(
                "SUMMARY_API_URL", "http://127.0.0.1:11434/v1/chat/completions"
            ).strip(),
            summary_model=os.getenv("SUMMARY_MODEL", "qwen3:8b").strip(),
            summary_timeout_seconds=_as_int("SUMMARY_TIMEOUT_SECONDS", 180, 10),
            summary_min_words=_as_int("SUMMARY_MIN_WORDS", 24, 1),
            summary_target_words=_as_int("SUMMARY_TARGET_WORDS", 70, 10),
            summary_max_words=_as_int("SUMMARY_MAX_WORDS", 110, 20),
            summary_temperature=_as_float("SUMMARY_TEMPERATURE", 0.1, 0.0),
        )
