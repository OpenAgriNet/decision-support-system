"""Resolves a farmer's colloquial scheme mention to the official scheme name
(issue #34).

A **pre-discovery hint**, not a governed-code source. Discovery routes on
``Ask.subject_categories`` alone, so what this buys is a canonical subject for
the planner to fill a request with and for the composer to name back to the
farmer. The planner still takes governed codes from ``describe_capability``
and nowhere else.

Deterministic on purpose. The vocabulary is a closed set of exact strings, so
a lookup answers it for no tokens on every turn, where an LLM tool call costs
two or three round trips on the critical path and fires only when the model
decides to.

Framework-agnostic: a plain mapping in, a plain ``SchemeResolution`` out. The
mapping is what ``ports.scheme_catalog.SchemeCatalog`` hands over, already
normalized and built once at boot.
"""

from __future__ import annotations

from collections.abc import Mapping

from dss.core.enrichment.models import Scheme, SchemeMatch, SchemeResolution
from dss.core.enrichment.normalize import normalize_tokens
from dss.core.intent.models import Ask, Intent, SubjectCategory

AliasIndex = Mapping[str, Scheme]


def find_scheme(text: str, aliases: AliasIndex) -> SchemeMatch | None:
    """The longest alias occurring in ``text`` as a whole-token span.

    Spans, not substrings: ``mif`` occurs inside "amplifier", and a substring
    scan would resolve a broken speaker to the Micro Irrigation Fund.

    Longest-first, so ``"makhana scheme"`` wins over the ``"makhana"`` inside
    it. That ordering is only unambiguous because no alias is shared between
    two schemes — the catalog adapter refuses to load one that is.
    """

    tokens = normalize_tokens(text)
    if not tokens or not aliases:
        return None

    # No span can be longer than the longest alias, so the scan is bounded by
    # the catalog rather than by however much a farmer typed.
    longest_alias = max(alias.count(" ") + 1 for alias in aliases)
    for span in range(min(len(tokens), longest_alias), 0, -1):
        for start in range(len(tokens) - span + 1):
            key = " ".join(tokens[start : start + span])
            scheme = aliases.get(key)
            if scheme is not None:
                return SchemeMatch(scheme=scheme, matched_alias=key)
    return None


def _resolve_ask(ask: Ask, query: str, aliases: AliasIndex) -> SchemeMatch | None:
    """The scheme this ask names, if any.

    The classifier's own ``agriculture_subjects`` is tried first because it is
    the only text that belongs to *this* ask: the raw query is shared by every
    ask in the turn, so searching it first would give both asks of "tell me
    about PKVY and the makhana scheme" whichever match happened to be longest.
    The query is the fallback, for when the classifier extracted a phrase the
    catalog does not list ("makhana") from a query that spells one out
    ("makhana scheme").
    """

    if ask.agriculture_subjects:
        match = find_scheme(ask.agriculture_subjects, aliases)
        if match is not None:
            return match
    return find_scheme(query, aliases)


def resolve_scheme_subjects(
    intent: Intent, query: str, aliases: AliasIndex
) -> SchemeResolution:
    """Rewrite every scheme ask's subject to its canonical scheme name.

    Only asks the classifier already categorised as ``Scheme`` are touched.
    That gate is what keeps "makhana scheme price in patna mandi" a market
    lookup: the query names a scheme, but the ask is not one, so nothing is
    rewritten. Issue #36 removes the gate once the catalog is trusted to hold
    no bare commodity words.

    Never adds, removes or reorders asks, and never touches ``confidence``:
    this refines what the classifier found, it does not classify. A scheme ask
    with no catalog entry is left carrying the farmer's own words — an
    unlisted scheme is not an error.
    """

    resolved: list[Ask] = []
    matches: list[SchemeMatch] = []
    for ask in intent.asks:
        match = (
            _resolve_ask(ask, query, aliases)
            if ask.subject_categories is SubjectCategory.SCHEME
            else None
        )
        if match is None:
            resolved.append(ask)
            continue
        matches.append(match)
        resolved.append(
            ask.model_copy(update={"agriculture_subjects": match.scheme.name})
        )

    if not matches:
        return SchemeResolution(intent=intent)
    return SchemeResolution(
        intent=intent.model_copy(update={"asks": tuple(resolved)}),
        matches=tuple(matches),
    )
