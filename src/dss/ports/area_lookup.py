"""The seam for turning a place name into a point — implemented by an adapter.

A farmer says "I am from Pune"; the discover call's spatial filter needs a
coordinate. Intent extracts the words, this port resolves them.

`resolve` returns a *list* so the three outcomes stay distinguishable, because
they need different handling and a single return value would collapse them:

- empty — no such area. The farmer named a village or city the index does not
  carry, so the caller reports the place as not found.
- one — resolved.
- many — the name is genuinely ambiguous (648 names in the shipped index
  collide, across and within states). Picking one silently can be a ~1000km
  error, so the caller narrows or asks which.

`region` is an optional ISO 3166-2 hint ("IN-MH"). It only narrows several
matches; it must not hide the one match a name has. It is absent often enough
that no implementation may require it.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import Geometry


class AreaMatch(BaseModel):
    """One area the name resolved to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # canonical spelling from the index, e.g. "Pune"
    region: str  # ISO 3166-2, e.g. "IN-MH"
    # Ancestor chain, coarsest first, no level words: ("India", "Maharashtra")
    # for a district, ("India", "Maharashtra", "Pune") for a block inside it.
    within: tuple[str, ...]
    geometry: Geometry


class AreaLookupUnavailable(Exception):
    """A source could not answer (down, slow, bad reply). Not the same as "no
    such area". The chain catches it; core never sees it."""


class AreaLookup(Protocol):
    async def resolve(
        self, name: str, region: str | None = None
    ) -> list[AreaMatch]: ...
