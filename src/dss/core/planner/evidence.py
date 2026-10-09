"""Builds ``Evidence`` from the answers the loop's tools accumulated.

Two consumers, two shapes: the model reads rendered markdown and cites it,
while ``Evidence`` carries typed data for the composer downstream. The
``select`` tool feeds both — it returns markdown and accumulates the raw
``DiscoveredAnswer`` on the agent's deps. This assembles the second.

``Result.data`` is a capability's ``resourceAttributes`` verbatim. Nothing
here reads, reshapes or validates them: each pack publishes its own shape
(``prices``, ``recommendations``, ``parameters``, ``services``) and they have
nothing in common past the JSON-LD envelope. Interpretation belongs to the
model and the composer, both of which read prose. So a capability the DSS has
never seen assembles with no code change.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from dss.core.intent.models import AmbiguousPlace, Intent, UnresolvedPlace
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.provider_discovery.models import DiscoveredAnswer


def place_failures(
    intent: Intent, *, place_optional: Collection[int] = ()
) -> list[tuple[int, Failure]]:
    """One failure per ask whose place has nowhere to search.

    Recorded before the planner runs, so the farmer hears about the ask even
    when the model never tries to call anything for it.

    An ask with no place at all fails too, unless `place_optional` names it:
    the pack serves it from nowhere (`core/planner/place.py`).
    """

    failures: list[tuple[int, Failure]] = []
    for index, ask in enumerate(intent.asks):
        if isinstance(ask.place, UnresolvedPlace):
            reason = f"{ask.place.unresolved_name}: place not found"
        elif isinstance(ask.place, AmbiguousPlace):
            reason = f"{ask.place.unresolved_name}: matches several places"
        elif ask.place is None and index not in place_optional:
            reason = "no place given, and this needs one"
        else:
            continue
        failures.append(
            (
                index,
                Failure(
                    ask_index=index, capability=None, reason=reason, retryable=False
                ),
            )
        )
    return failures


def assemble_evidence(
    raw_answers: Sequence[tuple[int, DiscoveredAnswer]],
    *,
    intent: Intent,
    direct_answers: Mapping[int, tuple[DiscoveredAnswer, ...]] | None = None,
    failures: Sequence[tuple[int, Failure]] = (),
) -> Evidence:
    """Turn everything the turn gathered into ``Evidence``.

    Two sources of answer, both ``DiscoveredAnswer``, so both assemble the
    same way:

    - ``raw_answers`` — what the loop's ``select`` calls returned.
    - ``direct_answers`` — ``DiscoveryResult.answers``, values the catalog
      already holds. These never appear in ``raw_answers``, because a Direct
      resource needs no call. Leaving them out meant an ask the catalog had
      already answered reached the planner's prompt and then vanished: the
      planner is told not to answer, and the composer never saw it.

    ``failures`` are calls that errored after the adapter's retries. They ride
    alongside the results so the composer can say "we could not reach
    Agmarknet" rather than "nobody serves this" — the same empty result, two
    different things to tell a farmer.

    Sources are numbered ``"1"``, ``"2"``, … in first-seen order, one per
    ``(provider, originator)`` pair:

    - A source is named by who authored the data, not who served it — a
      provider relaying IMD cites IMD, falling back to its own name.
    - Keyed on the pair, so one provider relaying two originators is two
      sources; ``sourceId`` is provider-scoped, so two providers relaying one
      originator stay two.
    - ``kind`` stays ``PROVIDER``: it says how the DSS reached the fact, which
      is not what ``name`` says.
    """

    source_id_by_origin: dict[tuple[str, str | None], str] = {}
    sources: list[Source] = []
    results: list[Result] = []

    direct = [
        (ask_index, answer)
        for ask_index, ask_answers in (direct_answers or {}).items()
        for answer in ask_answers
    ]

    for ask_index, answer in [*direct, *raw_answers]:
        origin = (answer.provider_id, answer.source_id)
        source_id = source_id_by_origin.get(origin)
        if source_id is None:
            source_id = str(len(sources) + 1)
            source_id_by_origin[origin] = source_id
            sources.append(
                Source(
                    id=source_id,
                    name=answer.source_name or answer.provider_name,
                    kind=SourceKind.PROVIDER,
                    url=answer.source_url,
                )
            )
        results.append(
            Result(
                ask_index=ask_index,
                source_id=source_id,
                data=dict(answer.attributes),
            )
        )

    served = tuple(dict.fromkeys(ask_index for ask_index, _ in [*direct, *raw_answers]))
    return Evidence(
        sources=tuple(sources),
        results=tuple(results),
        served=served,
        failed=tuple(failure for _, failure in failures),
        sufficient=bool(results),
    )
