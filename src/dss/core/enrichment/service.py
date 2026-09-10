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

import difflib
from collections.abc import Mapping

from dss.core.enrichment.models import Scheme, SchemeMatch, SchemeResolution
from dss.core.enrichment.normalize import alias_key, normalize_tokens
from dss.core.intent.models import Ask, Intent, SubjectCategory

AliasIndex = Mapping[str, Scheme]

# Off unless a threshold is supplied. `None` rather than a module default so
# there is exactly one place the number lives — `Settings` — and a caller that
# forgot to pass it gets exact matching rather than a second, invisible
# default that disagrees with the configured one.
NO_FUZZY_MATCHING = None


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


def find_similar_scheme(
    text: str, aliases: AliasIndex, threshold: float
) -> SchemeMatch | None:
    """The closest alias to ``text`` above ``threshold``, if any.

    For a misspelling — "makna" for "makhana". Compares the *whole* normalized
    text against whole aliases, so it is one comparison set per call: running
    it over every token span instead would multiply both the cost and the
    chances of a wrong hit, and a misspelling is what the classifier extracts
    as the subject anyway.

    ``difflib`` rather than ``rapidfuzz``: at this catalog's size the speed
    difference is unmeasurable and a C extension is not worth a dependency.
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

    The classifier's own ``agriculture_subjects`` is tried first because it is
    the only text that belongs to *this* ask: the raw query is shared by every
    ask in the turn, so searching it first would give both asks of "tell me
    about PKVY and the makhana scheme" whichever match happened to be longest.

    The raw query is a fallback **only for an ask already categorised as a
    scheme** — for when the classifier extracted a phrase the catalog does not
    list ("makhana") from a query that spells one out ("makhana scheme").

    That restriction is what makes overriding the category safe. A match now
    rewrites an ask's category, so letting a non-scheme ask fall back to the
    shared query would let one scheme mention anywhere in a turn convert every
    other ask in it: "wheat price and the makhana scheme" would turn the wheat
    market lookup into a scheme ask. A non-scheme ask is therefore judged on
    its own extracted subject and nothing else.
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
    # Last resort, and only against the ask's own subject: the farmer may have
    # misspelled the scheme. Never against the query — a long sentence scores
    # poorly against a two-word alias, so the ratio would be meaningless.
    return find_similar_scheme(ask.agriculture_subjects, aliases, threshold)


def resolve_scheme_subjects(
    intent: Intent,
    query: str,
    aliases: AliasIndex,
    *,
    fuzzy_threshold: float | None = NO_FUZZY_MATCHING,
) -> SchemeResolution:
    """Rewrite a matched ask's subject to its canonical scheme name, and its
    category to ``Scheme``.

    The category override is the point. ``subject_categories`` is the only
    thing provider discovery routes on, and the classifier does not know that
    "PKVY" or "dhan dhaanya" names a scheme — so a mis-categorised ask is sent
    to the wrong capability type and no later step can recover it. An alias
    hit is better evidence than the classifier's guess.

    This is safe **only because every alias in the catalog is
    scheme-distinctive.** A bare commodity word would convert crop and market
    asks wholesale: with ``makhana`` listed, "makhana price in patna mandi"
    becomes a scheme ask. Nothing here enforces that — the catalog is
    tenant-authored domain data (ADR-0007 §5) — so the restriction on the
    query fallback in ``_resolve_ask`` is the one structural guard, and the
    trace line in ``orchestration/turn.py`` is the only breadcrumb.

    ``interaction_type`` is left alone: a mis-categorised ask usually still
    has the right verb ("how do I *apply* for PKVY" is an ``act`` either way).

    ``fuzzy_threshold`` opts into similarity matching for misspellings, tried
    only after every exact lookup has missed and only against an ask's own
    extracted subject. Unset, matching is exact.

    Never adds, removes or reorders asks, and never touches ``confidence``:
    this refines what the classifier found, it does not classify. A scheme ask
    with no catalog entry is left carrying the farmer's own words — an
    unlisted scheme is not an error.
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
