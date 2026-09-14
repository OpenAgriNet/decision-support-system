"""Resolves a colloquial scheme mention to the official scheme name (#34).

A pre-discovery hint, not a governed-code source — the planner still takes
governed codes from ``describe_capability`` alone. A closed set of exact
strings, so a lookup answers it for no tokens on every turn.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping

from dss.core.enrichment.models import Scheme, SchemeMatch, SchemeResolution
from dss.core.enrichment.normalize import alias_key, normalize_tokens
from dss.core.intent.models import Ask, Intent, SubjectCategory

AliasIndex = Mapping[str, Scheme]

# Off unless a threshold is supplied, so the number lives in `Settings` alone
# and no module default can quietly disagree with the configured one.
NO_FUZZY_MATCHING = None


def find_scheme(text: str, aliases: AliasIndex) -> SchemeMatch | None:
    """The longest alias occurring in ``text`` as a whole-token span.

    Spans, not substrings: ``mif`` occurs inside "amplifier". Longest-first is
    unambiguous only because the adapter refuses a shared alias.
    """

    tokens = normalize_tokens(text)
    if not tokens or not aliases:
        return None

    # Bounded by the catalog's longest alias, not by however much a farmer typed.
    longest_alias = max(alias.count(" ") + 1 for alias in aliases)
    for span in range(min(len(tokens), longest_alias), 0, -1):
        for start in range(len(tokens) - span + 1):
            key = " ".join(tokens[start : start + span])
            scheme = aliases.get(key)
            if scheme is not None:
                return SchemeMatch(scheme=scheme, matched_alias=key)
    return None


def find_similar_scheme(
    text: str, aliases: AliasIndex, threshold: float
) -> SchemeMatch | None:
    """The closest alias to ``text`` above ``threshold`` — "makna" for "makhana".

    Whole text against whole aliases: scoring every token span would multiply
    cost and wrong hits. ``difflib`` over ``rapidfuzz`` — at this catalog size
    a C extension is not worth a dependency.
    """

    key = alias_key(text)
    if not key or not aliases:
        return None
    closest = difflib.get_close_matches(key, aliases, n=1, cutoff=threshold)
    if not closest:
        return None
    return SchemeMatch(scheme=aliases[closest[0]], matched_alias=closest[0], fuzzy=True)


def _resolve_ask(
    ask: Ask, query: str, aliases: AliasIndex, threshold: float | None
) -> SchemeMatch | None:
    """The scheme this ask names, if any.

    The ask's own subject first — the query is shared, so both asks of "PKVY
    and the makhana scheme" would take the longest match. The query is a
    fallback only for an already-scheme ask, or one mention would convert
    every other ask in the turn.
    """

    if ask.agriculture_subjects:
        match = find_scheme(ask.agriculture_subjects, aliases)
        if match is not None:
            return match
    if ask.subject_categories is not SubjectCategory.SCHEME:
        return None
    match = find_scheme(query, aliases)
    if match is not None or threshold is None or not ask.agriculture_subjects:
        return match
    # Last resort, and only against the ask's own subject: a long sentence
    # scores meaninglessly against a two-word alias.
    return find_similar_scheme(ask.agriculture_subjects, aliases, threshold)


def resolve_scheme_subjects(
    intent: Intent,
    query: str,
    aliases: AliasIndex,
    *,
    fuzzy_threshold: float | None = NO_FUZZY_MATCHING,
) -> SchemeResolution:
    """Rewrite a matched ask's subject to the canonical scheme name and its
    category to ``Scheme``.

    The override is the point: discovery routes on ``subject_categories``
    alone, and an alias hit beats a classifier that does not know "PKVY" names
    a scheme. Safe only because every alias is scheme-distinctive (ADR-0008
    §5: tenant-enforced). ``interaction_type``, ask order and ``confidence``
    are untouched; an unlisted scheme keeps the farmer's words.
    """

    resolved: list[Ask] = []
    matches: list[SchemeMatch] = []
    for ask in intent.asks:
        match = _resolve_ask(ask, query, aliases, fuzzy_threshold)
        if match is None:
            resolved.append(ask)
            continue
        matches.append(match)
        resolved.append(
            ask.model_copy(
                update={
                    "agriculture_subjects": match.scheme.name,
                    "subject_categories": SubjectCategory.SCHEME,
                }
            )
        )

    if not matches:
        return SchemeResolution(intent=intent)
    return SchemeResolution(
        intent=intent.model_copy(update={"asks": tuple(resolved)}),
        matches=tuple(matches),
    )
