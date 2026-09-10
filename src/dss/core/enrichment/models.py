"""The scheme-resolution contract (issue #34). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dss.core.intent.models import Intent


class Scheme(BaseModel):
    """One government scheme as the catalog knows it.

    ``code`` is the catalog's stable row key. Nothing reads it at runtime yet
    — the resolved ``Ask`` carries only ``name`` — but names and aliases get
    edited and the code is what a future mapping onto the network's own
    governed vocabulary would key on, so it is carried rather than dropped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    name: str


class SchemeMatch(BaseModel):
    """One alias hit. ``matched_alias`` is the *normalized* text that matched,
    not the farmer's raw words — it is what a trace needs to explain why this
    scheme was chosen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Scheme
    matched_alias: str


class SchemeResolution(BaseModel):
    """The enriched intent and what was matched to produce it.

    The matches are returned rather than only logged because the rewrite is
    otherwise invisible: the ``Ask`` that comes out no longer contains the
    words the farmer used, and something has to be able to say why.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    matches: tuple[SchemeMatch, ...] = ()
