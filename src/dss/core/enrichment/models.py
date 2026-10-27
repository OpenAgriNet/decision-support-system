"""The scheme-resolution contract (issue #34). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dss.core.intent.models import Intent, Scheme


class SchemeMatch(BaseModel):
    """One alias hit. ``matched_alias`` is the normalized text that matched,
    not the farmer's raw words. ``fuzzy`` marks a similarity hit — the one
    failure this mechanism can produce, so a trace must tell it from an exact
    one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Scheme
    matched_alias: str
    fuzzy: bool = False


class SchemeResolution(BaseModel):
    """The enriched intent and what was matched to produce it.

    Matches are returned, not only logged: the ``Ask`` that comes out no
    longer holds the farmer's words, and something has to say why.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    matches: tuple[SchemeMatch, ...] = ()
