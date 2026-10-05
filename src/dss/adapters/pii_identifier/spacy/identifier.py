"""People's names by spaCy's ``en_core_web_sm``, behind the ``PiiIdentifier`` port.

Small (about 15 MB, plus spaCy) and fast (about 2 ms a sentence), but English
only. On romanised Hinglish it misses names and tags ordinary words as people
("mera naam", "gehu"). spaCy gives no confidence score, so nothing can filter
those out. Recorded in ADR-0016.
"""

from __future__ import annotations

from collections.abc import Sequence

import anyio.to_thread
import spacy

from dss.adapters.pii_identifier.spacy.models import SpacySettings
from dss.core.redaction.models import PiiSpan
from dss.ports.pii_identifier import IdentifierUnavailable

# Name finding needs only the token vectors and the entity recogniser.
_UNUSED = ["tagger", "parser", "attribute_ruler", "lemmatizer"]
_PERSON = "PERSON"


class SpacyIdentifier:
    name = "spacy"

    def __init__(self, settings: SpacySettings) -> None:
        try:
            self._nlp = spacy.load(settings.model, exclude=_UNUSED)
        except OSError as exc:
            raise IdentifierUnavailable(
                f"spaCy model '{settings.model}' is not installed — run `uv sync`"
            ) from exc
        self._entity = settings.entity

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        # CPU-bound: keep it off the event loop.
        return await anyio.to_thread.run_sync(self._identify, list(texts))

    def _identify(self, texts: list[str]) -> list[list[PiiSpan]]:
        return [
            [
                PiiSpan(
                    start=ent.start_char,
                    end=ent.end_char,
                    entity=self._entity,
                    score=1.0,
                    source=self.name,
                    value=ent.text,
                )
                for ent in doc.ents
                if ent.label_ == _PERSON
            ]
            for doc in self._nlp.pipe(texts)
        ]
