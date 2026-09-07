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

from collections.abc import Sequence

from dss.core.intent.models import Intent
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.provider_discovery.models import DiscoveredAnswer


def assemble_evidence(
    raw_answers: Sequence[tuple[int, DiscoveredAnswer]],
    *,
    intent: Intent,
    failures: Sequence[tuple[int, Failure]] = (),
) -> Evidence:
    """Turn the loop's accumulated answers into ``Evidence``.

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

    for ask_index, answer in raw_answers:
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

    served = tuple(dict.fromkeys(ask_index for ask_index, _ in raw_answers))
    return Evidence(
        sources=tuple(sources),
        results=tuple(results),
        served=served,
        failed=tuple(failure for _, failure in failures),
        sufficient=bool(results),
    )
