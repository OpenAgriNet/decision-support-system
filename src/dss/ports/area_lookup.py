"""The seam for turning a place name into a point — implemented by an adapter.

A farmer says "I am from Pune"; the discover call's spatial filter needs a
coordinate. Intent extracts the words, this port resolves them.

`resolve` returns a *list* so the three outcomes stay distinguishable, because
they need different handling and a single return value would collapse them:

- empty — no such area. The farmer named a village or city the index does not
  carry, so the caller should ask for a district.
- one — resolved.
- many — the name is genuinely ambiguous (three district names in India collide:
  Bilaspur, Hamirpur, Pratapgarh). Picking one silently is a ~1000km error, so
  the caller asks which.

`region` is an optional ISO 3166-2 hint ("IN-MH"). It disambiguates all three
collisions when the turn carries it, and is absent often enough that no
implementation may require it.
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
    geometry: Geometry


class AreaLookup(Protocol):
    def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]: ...
