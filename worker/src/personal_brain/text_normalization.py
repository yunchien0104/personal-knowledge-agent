from __future__ import annotations

import re
import unicodedata


_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\u31f0-\u31ff\uac00-\ud7af"
_CJK_LINE_BREAK = re.compile(rf"(?<=[{_CJK}])[ \t]*\n[ \t]*(?=[{_CJK}])")
_WORD_LINE_BREAK = re.compile(r"(?<=[A-Za-z0-9,;:)])[ \t]*\n[ \t]*(?=[A-Za-z0-9(])")
_DEHYPHENATE = re.compile(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[a-z])")
_HORIZONTAL_SPACE = re.compile(r"[\t\f\v\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def preserve_extracted_text(text: str) -> str:
    """Preserve the extractor's citation text, only canonicalizing line endings."""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_for_retrieval(text: str) -> str:
    """Build a Unicode-normalized search representation without changing Evidence."""

    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _DEHYPHENATE.sub("", normalized)
    normalized = _CJK_LINE_BREAK.sub("", normalized)
    normalized = _WORD_LINE_BREAK.sub(" ", normalized)
    normalized = _HORIZONTAL_SPACE.sub(" ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = _EXCESS_BLANK_LINES.sub("\n\n", normalized)
    return normalized.strip()


def query_keywords(text: str, *, limit: int = 24) -> list[str]:
    """Return bounded language-neutral terms, including CJK uni/bi-grams."""

    normalized = normalize_for_retrieval(text).casefold()
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in seen and len(terms) < limit:
            seen.add(value)
            terms.append(value)

    for token in re.findall(r"[a-z0-9][a-z0-9._+-]{1,79}", normalized):
        add(token)
    for run in re.findall(rf"[{_CJK}]+", normalized):
        if len(run) == 1:
            add(run)
            continue
        for index in range(len(run) - 1):
            add(run[index:index + 2])
        for character in run:
            add(character)
    return terms


def lexical_search_text(text: str) -> str:
    normalized = normalize_for_retrieval(text)
    terms = query_keywords(normalized)
    return " ".join([normalized, *terms]).strip()
