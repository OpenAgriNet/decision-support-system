"""The speed benchmark's fixed question set, read from `questions.toml`."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    region: str
    area: str
    # (lon, lat), sent as the turn's coordinates. Only weather carries one.
    point: tuple[float, float] | None


# The languages the set holds text in. Adding one means adding its questions.
_LANGUAGES = ("en",)


def load_questions(path: Path, lang: str = "en") -> list[Question]:
    if lang not in _LANGUAGES:
        raise ValueError(f"no questions in {lang!r}; known: {', '.join(_LANGUAGES)}")
    with path.open("rb") as file:
        entries = tomllib.load(file)["question"]
    return [
        Question(
            id=e["id"],
            category=e["category"],
            text=e["question"],
            region=e["region"],
            area=e["area"],
            point=(e["lon"], e["lat"]) if "lon" in e else None,
        )
        for e in entries
    ]
