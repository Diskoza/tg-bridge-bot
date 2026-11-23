from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings


def main() -> None:
    from faster_whisper import WhisperModel

    settings = Settings.load()
    print(f"Загрузка модели {settings.whisper_model} в {settings.model_dir}")
    WhisperModel(
        settings.whisper_model,
        device="cpu",
        compute_type=settings.whisper_compute_type,
        cpu_threads=settings.whisper_cpu_threads,
        num_workers=1,
        download_root=str(settings.model_dir),
    )
    print("Модель готова.")


if __name__ == "__main__":
    main()
