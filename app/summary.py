from __future__ import annotations

import logging
import math
import re
from collections import Counter

import httpx

from .config import Settings
from .text_cleanup import clean_transcript


LOGGER = logging.getLogger(__name__)
_WORD_RE = re.compile(r"(?iu)[а-яёa-z0-9]+(?:[-'][а-яёa-z0-9]+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)")
_PREFIX_RE = re.compile(
    r"(?iu)^\s*(?:краткая\s+суть|суть|краткое\s+содержание|резюме|ответ)\s*[:—-]\s*"
)
_STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "было", "быть", "в", "вам", "вас",
    "весь", "во", "вот", "все", "всё", "вы", "где", "да", "даже", "для", "до",
    "его", "ее", "её", "если", "есть", "ещё", "же", "за", "здесь", "и", "из", "или",
    "им", "их", "к", "как", "когда", "кто", "ли", "мне", "мы", "на", "над", "надо",
    "не", "него", "нее", "ней", "нет", "но", "ну", "о", "об", "один", "она", "они",
    "оно", "от", "по", "под", "при", "про", "с", "сам", "себе", "себя", "так", "там",
    "то", "тоже", "только", "ты", "у", "уже", "чем", "что", "чтобы", "это", "я",
}
_IMPORTANT_MARKERS = {
    "потому", "поэтому", "значит", "нужно", "надо", "должен", "должна", "проблема",
    "главное", "итого", "всего", "сумме", "работа", "сегодня", "завтра", "вчера",
}


class SummaryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def prepare(self, transcript: str) -> str:
        cleaned = clean_transcript(transcript)
        words = _WORD_RE.findall(cleaned)
        if len(words) < self.settings.summary_min_words:
            return cleaned

        if self.settings.summary_backend == "local_llm":
            try:
                generated = await self._local_llm_summary(cleaned, strict=False)
                validated = self._validate_generated(cleaned, generated)
                if validated:
                    return validated

                generated_strict = await self._local_llm_summary(cleaned, strict=True)
                validated_strict = self._validate_generated(cleaned, generated_strict)
                if validated_strict:
                    return validated_strict

                sanitized = self._drop_sentences_with_new_numbers(
                    cleaned, generated_strict or generated
                )
                if sanitized:
                    return sanitized
            except Exception:
                LOGGER.exception("Локальная модель не смогла подготовить краткую суть")

        return extractive_summary(
            cleaned,
            target_words=self.settings.summary_target_words,
            max_words=self.settings.summary_max_words,
        )

    async def _local_llm_summary(self, transcript: str, *, strict: bool) -> str:
        source_numbers = sorted({_normalize_number(x) for x in _NUMBER_RE.findall(transcript)})
        number_rule = (
            "Разрешённые числовые значения: "
            + (", ".join(source_numbers) if source_numbers else "чисел нет")
            + ". Используй только эти значения. Не складывай их, не вычитай и не придумывай новые. "
            if strict
            else "Не придумывай и не пересчитывай числа: сохраняй только явно сказанные значения. "
        )
        system_prompt = (
            "Ты превращаешь неточную автоматическую расшифровку русского голосового "
            "сообщения в короткую и понятную передачу смысла. Исправляй очевидные ошибки "
            "распознавания только тогда, когда правильный вариант однозначно следует из "
            "контекста. Передай только сказанные факты, без догадок, советов и нового контекста. "
            "Сохрани имена, отрицания, причины, планы, суммы, валюты и порядок событий. "
            + number_rule
            + "Удали повторы, слова-паразиты, оговорки и ругательства, если они не меняют смысл. "
            "Можно переставлять фразы и исправлять разговорную грамматику. Если фрагмент "
            "совсем непонятен, лучше опусти его, а не выдумывай. Ответь одним связным абзацем "
            "на русском языке, без заголовка, кавычек и комментариев. "
            f"Желательная длина — до {self.settings.summary_target_words} слов, "
            f"абсолютный максимум — {self.settings.summary_max_words} слов."
        )
        payload = {
            "model": self.settings.summary_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": "Автоматическая расшифровка:\n\n" + transcript,
                },
            ],
            "temperature": 0.0,
            "top_p": 0.85,
            "max_tokens": 300,
            "reasoning_effort": "none",
            "stream": False,
        }
        timeout = httpx.Timeout(self.settings.summary_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.settings.summary_api_url, json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    def _validate_generated(self, source: str, generated: str) -> str | None:
        value = _PREFIX_RE.sub("", generated.strip().strip('"“”'))
        value = clean_transcript(value)
        words = _WORD_RE.findall(value)
        if not value or len(words) < 4 or len(words) > self.settings.summary_max_words + 15:
            return None

        source_numbers = {_normalize_number(item) for item in _NUMBER_RE.findall(source)}
        generated_numbers = {_normalize_number(item) for item in _NUMBER_RE.findall(value)}
        if not generated_numbers.issubset(source_numbers):
            LOGGER.warning(
                "Отклонена суть с новыми числами: source=%s generated=%s",
                sorted(source_numbers),
                sorted(generated_numbers),
            )
            return None

        suspicious = (
            "как языковая модель",
            "я не могу",
            "в расшифровке говорится",
            "пользователь говорит",
            "автоматическая расшифровка",
        )
        if any(fragment in value.lower() for fragment in suspicious):
            return None

        source_tokens = {
            token.lower() for token in _WORD_RE.findall(source)
            if token.lower() not in _STOPWORDS and len(token) > 2
        }
        generated_tokens = {
            token.lower() for token in _WORD_RE.findall(value)
            if token.lower() not in _STOPWORDS and len(token) > 2
        }
        if generated_tokens and len(source_tokens & generated_tokens) / len(generated_tokens) < 0.28:
            return None
        return value

    def _drop_sentences_with_new_numbers(self, source: str, generated: str) -> str | None:
        source_numbers = {_normalize_number(item) for item in _NUMBER_RE.findall(source)}
        parts = [part.strip() for part in _SENTENCE_RE.split(generated) if part.strip()]
        kept: list[str] = []
        for part in parts:
            numbers = {_normalize_number(item) for item in _NUMBER_RE.findall(part)}
            if numbers.issubset(source_numbers):
                kept.append(part)
        value = clean_transcript(" ".join(kept))
        if len(_WORD_RE.findall(value)) < 8:
            return None
        return _truncate_at_word_boundary(value, self.settings.summary_max_words)


def extractive_summary(text: str, target_words: int = 70, max_words: int = 110) -> str:
    cleaned = clean_transcript(text)
    all_words = _WORD_RE.findall(cleaned)
    if len(all_words) <= target_words:
        return cleaned

    units = _split_units(cleaned)
    if len(units) <= 1:
        return _truncate_at_word_boundary(cleaned, max_words)

    tokenized = []
    frequencies: Counter[str] = Counter()
    for unit in units:
        tokens = [token.lower() for token in _WORD_RE.findall(unit)]
        meaningful = [token for token in tokens if token not in _STOPWORDS and len(token) > 2]
        tokenized.append((tokens, meaningful))
        frequencies.update(set(meaningful))

    scores: list[tuple[float, int]] = []
    for index, (unit, (tokens, meaningful)) in enumerate(zip(units, tokenized)):
        base = sum(1.0 + math.log1p(frequencies[token]) for token in meaningful)
        base /= max(4.0, math.sqrt(len(tokens) or 1))
        lowered = set(tokens)
        base += 2.8 * len(_NUMBER_RE.findall(unit))
        if {"злотых", "злотый", "рублей", "рубля", "евро", "долларов"} & lowered:
            base += 2.0
        base += 0.7 * len(_IMPORTANT_MARKERS & lowered)
        if any(token in lowered for token in {"не", "нельзя", "без", "никогда"}):
            base += 0.6
        if index == 0:
            base += 0.25
        scores.append((base, index))

    desired = min(max_words, max(target_words, int(len(all_words) * 0.48)))
    selected: set[int] = set()
    used_words = 0

    mandatory = [index for index, unit in enumerate(units) if _NUMBER_RE.search(unit)]
    for index in mandatory:
        count = len(tokenized[index][0])
        if used_words + count <= max_words or not selected:
            selected.add(index)
            used_words += count

    for _, index in sorted(scores, reverse=True):
        if index in selected:
            continue
        count = len(tokenized[index][0])
        if used_words >= desired:
            break
        if used_words + count <= max_words:
            selected.add(index)
            used_words += count

    if not selected:
        selected.add(max(scores)[1])

    result = " ".join(units[index].strip() for index in sorted(selected))
    result = clean_transcript(result)
    return _truncate_at_word_boundary(result, max_words)


def _split_units(text: str) -> list[str]:
    sentences = [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]
    units: list[str] = []
    for sentence in sentences:
        words = _WORD_RE.findall(sentence)
        if len(words) <= 32:
            units.append(sentence)
            continue
        parts = re.split(
            r"(?iu)(?=\s+(?:но|а|потому что|поэтому|то есть|значит|потом|при этом|в итоге)\s+)",
            sentence,
        )
        units.extend(part.strip(" ,") for part in parts if part.strip(" ,"))
    return units


def _truncate_at_word_boundary(text: str, max_words: int) -> str:
    matches = list(_WORD_RE.finditer(text))
    if len(matches) <= max_words:
        return text
    cutoff = matches[max_words - 1].end()
    return text[:cutoff].rstrip(" ,;:-–—") + "…"


def _normalize_number(value: str) -> str:
    return value.replace(",", ".").lstrip("0") or "0"
