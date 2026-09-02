"""The types intent recognition produces, and the taxonomy it labels against.

Intent turns one farmer sentence into labels the rest of the pipeline reads. It
does not answer, fetch, or choose who to call — it only labels. Three questions,
three labels:

- **How many questions?** One sentence often holds several — each becomes an
  ``Ask``.
- **What is each about?** A ``category`` (from the taxonomy) and the ``subject``
  if the farmer named one.
- **What kind of answer is wanted?** ``ActionType`` — advice, a lookup, or an
  action.

Everything after this reads the labels, not the raw query, which stays whole on
``UserTurn``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    """What kind of answer an ask wants."""

    ADVISORY = "advisory"  # guidance / recommendation ("when should I sow?")
    LOOKUP = "lookup"  # a fact retrieved ("what is the price of potato?")
    ACT = "act"  # perform an action on the farmer's behalf


class Ask(BaseModel):
    """One question inside a turn, labelled.

    Carries no query text — the query stays whole on ``UserTurn``. One ask
    becomes one plan step downstream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str | None
    """The farmer's own word for the thing asked about — "potato", "PM-KISAN".

    ``None`` when the question names no subject ("will it rain?"). Nothing here
    resolves it to an id; it stays the raw word (``DSS_ARCHITECTURE.md`` §8.3
    open item #7).
    """

    category: str
    """One of the taxonomy's categories. An ask whose category is not in the
    active taxonomy is dropped before it reaches here (see ``service.classify``).
    """

    action_type: ActionType


class Intent(BaseModel):
    """The full set of labels for one turn.

    An **empty** ``asks`` means "understood nothing" — a valid answer, not an
    error. And ``confidence`` is independent of ask count: three asks do not
    imply low confidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asks: tuple[Ask, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)


class Taxonomy(BaseModel):
    """The set of categories an ask may be labelled with, for one tenant.

    The DSS ships a base taxonomy (``BASE_TAXONOMY``); adopters extend it
    (``DSS_ARCHITECTURE.md`` §5.2). Matching is case-insensitive so a model that
    returns ``"market"`` still resolves to the canonical ``"Market"``; a
    category that resolves to nothing is not in the taxonomy and its ask is
    dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: tuple[str, ...]

    def resolve(self, name: str) -> str | None:
        """Return the canonical category matching ``name``, or ``None``.

        Case- and whitespace-insensitive. ``None`` means "not in this taxonomy".
        """
        key = name.strip().casefold()
        for canonical in self.categories:
            if canonical.casefold() == key:
                return canonical
        return None

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.resolve(name) is not None


# The base taxonomy the DSS ships for the Agriculture and Livestock domain.
# Adopters extend it locally (§5.2); this is the default an orchestration wiring
# uses when a tenant declares nothing more specific.
BASE_TAXONOMY = Taxonomy(
    categories=(
        "Crop",
        "Livestock",
        "Weather",
        "Market",
        "Scheme",
        "Knowledge",
        "Service",
    )
)
