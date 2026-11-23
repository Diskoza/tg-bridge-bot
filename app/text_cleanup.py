from __future__ import annotations

import re


_INTERJECTIONS = re.compile(
    r"(?iu)(?<!\w)(?:ээ+|эм+|мм+|аа+|эээм+|ну+|yyy+|eee+|hmm+|uh+|um+)(?!\w)[,;:\-–—\s]*"
)
_REPEATED_WORD = re.compile(r"(?iu)\b([\wąćęłńóśźżіїєґа-яё]+)(?:\s+\1\b)+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_MANY_SPACES = re.compile(r"[ \t]{2,}")
_MANY_BLANKS = re.compile(r"\n{3,}")
_REPEATED_PUNCT = re.compile(r"([,.!?])\1+")


def clean_transcript(text: str) -> str:
    value = text.strip()
    value = _INTERJECTIONS.sub("", value)
    value = _REPEATED_WORD.sub(r"\1", value)
    value = _SPACE_BEFORE_PUNCT.sub(r"\1", value)
    value = _REPEATED_PUNCT.sub(r"\1", value)
    value = _MANY_SPACES.sub(" ", value)
    value = _MANY_BLANKS.sub("\n\n", value)
    value = value.strip(" ,;:-–—")
    if value and value[0].isalpha():
        value = value[0].upper() + value[1:]
    return value
