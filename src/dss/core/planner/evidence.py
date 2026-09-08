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

from collections.abc import Mapping, Sequence

from dss.core.intent.models import Intent
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.provider_discovery.models import DiscoveredAnswer


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
    distinct provider: a provider answering two asks is one citable source,
    which is how the composer will cite it. ``url`` is always ``None`` —
    discovery carries no provider URL, and mining a pack's own ``source``
    block for one is a follow-up.
    """

    source_id_by_provider: dict[str, str] = {}
    sources: list[Source] = []
    results: list[Result] = []

    direct = [
        (ask_index, answer)
        for ask_index, ask_answers in (direct_answers or {}).items()
        for answer in ask_answers
    ]

    for ask_index, answer in [*direct, *raw_answers]:
        source_id = source_id_by_provider.get(answer.provider_id)
        if source_id is None:
            source_id = str(len(sources) + 1)
            source_id_by_provider[answer.provider_id] = source_id
            sources.append(
                Source(
                    id=source_id,
                    name=answer.provider_name,
                    kind=SourceKind.PROVIDER,
                    url=None,
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
