"""The tool-calling agent loop that stands in for the planner/executioner
pair (ADR-0005). ``core/planner/`` does the work; this module wires it to
Pydantic AI.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import anyio
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from dss.core.intent.models import (
    AmbiguousPlace,
    Intent,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.moderation.models import Outcome
from dss.core.planner.describe_capability import render_candidates_as_markdown
from dss.core.planner.lookup import find_capability
from dss.core.planner.markdown import render_answers_as_markdown
from dss.core.planner.models import Failure, Skill, Verdict
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.planner.skills import tool_names_for
from dss.core.planner.validation import (
    DomainSchema,
    InvalidArgumentError,
    validate_arguments,
)
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
)
from dss.core.provider_preferences.service import priority_for_ask
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import UserTurn
from dss.observability.trace_log import log_event
from dss.ports.invocation import CapabilityInvocation, SelectFailedError


@dataclass
class PlannerDeps:
    """Everything the agent's tools need for one turn. A plain object passed
    to ``agent.run(deps=...)`` — tools read it via ``RunContext.deps``."""

    turn: UserTurn
    # The ask an `ask_index` names, so `select` can state the category that was
    # asked about rather than the one the provider advertised.
    intent: Intent
    discovery: DiscoveryResult
    schemas: dict[str, DomainSchema]
    schema_context_index: dict[str, str]
    # Required, not optional: the tool cannot work without it, and an
    # `assert` guard would be stripped by `python -O`, turning a wiring
    # mistake into an AttributeError from inside a tool body.
    invocation: CapabilityInvocation
    verdict: Verdict
    # This turn's real values, for the provider call only. Handed straight to
    # `select`; nothing in a tool reads it. Holds nothing unless handed one, so
    # a caller that forgets it sends tags, not values.
    visibility: Visibility = NOTHING_HELD
    # TODO(#55): remove with the pack fix — only the MandiPrice validity
    # exception in `build_resource_attributes` reads this. Injected rather
    # than called inside core so a test can fix the day.
    today: Callable[[], date] = lambda: datetime.now(UTC).date()
    raw_answers: list[tuple[int, DiscoveredAnswer]] = field(default_factory=list)
    failures: list[tuple[int, Failure]] = field(default_factory=list)


async def _select(
    ctx: RunContext[PlannerDeps],
    ask_index: int,
    resource_id: str,
    resource_attributes: dict,
) -> str:
    """Read a chosen capability's data. ``ask_index`` and ``resource_id``
    together pick one candidate from discovery; ``resource_attributes``
    carries only what the model resolves or authors (a governed code, free
    text) — structural fields are assembled here, not by the model.

    With a client ranking in force (ADR-0019) this is one call per *ask*
    rather than per candidate: the chosen candidate names the @type whose
    fields the model authored, and every ranked provider serving that @type
    is consulted with them. The answer is built from the first ranked
    provider that returned anything; the rest are logged and dropped. Which
    provider answers is therefore the configuration's decision, not the
    model's — the model is picking a capability, not a vendor."""

    deps = ctx.deps
    decision = await deps.verdict.get()
    if decision.outcome is not Outcome.PROCEED:
        # Not a ModelRetry: retrying cannot clear a moderation rejection.
        return "This turn was not cleared to call providers. Do not retry."

    capability = find_capability(
        deps.discovery, ask_index=ask_index, resource_id=resource_id
    )
    if capability is None:
        raise ModelRetry(
            f"resource_id {resource_id!r} is not one of ask {ask_index}'s candidates"
        )

    # Safe by here: `find_capability` has already matched `ask_index` against
    # discovery, which is keyed off these same asks.
    ask = deps.intent.asks[ask_index]

    # A ModelRetry, not a KeyError. Discovery reports what the network
    # offers; the schema index is built from the packs on disk, and the two
    # can disagree — a skipped pack leaves the index without a @type the
    # network still advertises. Without a schema there is nothing to validate
    # the model's fields against and no way to build a request, but that is a
    # reason for the model to pick another candidate, not to end the turn.
    schema = deps.schemas.get(capability.capability)
    if schema is None:
        raise ModelRetry(
            f"no schema is loaded for {capability.capability}, so it cannot be "
            f"called. Pick another candidate for ask {ask_index}, or report "
            f"that this ask has no answer."
        )

    # No call without a place the pack needs: it would go out with no
    # location and could come back for somewhere else. An ask naming nowhere
    # is fine for a pack that indexes no location (an advisory), and the
    # schema is what says which. `plan()` already recorded the failure.
    # Not a ModelRetry: retrying cannot fix the place.
    if isinstance(ask.place, AmbiguousPlace | UnresolvedPlace) or (
        ask.place is None and schema.needs_place
    ):
        return (
            f"Ask {ask_index} has no place to search. Do not call select for "
            f"it; report that this ask has no answer."
        )

    try:
        validate_arguments(resource_attributes, schema)
    except InvalidArgumentError as exc:
        raise ModelRetry(str(exc)) from exc

    # Who this one call consults. With a client ranking in force that is
    # every ranked provider for the ask, best first; without one it is the
    # single candidate the model named, exactly as before (ADR-0019).
    consulting = _consulting(deps, ask_index=ask_index, capability=capability)

    # Built per provider, not once and reused: `build_resource_attributes`
    # echoes each capability's own advertised values back into the request,
    # so two providers serving the same @type need two different bodies out
    # of the same model-authored fields.
    def attributes_for(candidate: ProviderCapability) -> dict:
        return build_resource_attributes(
            capability=candidate,
            subject_category=ask.subject_categories.value,
            place=ask.place if isinstance(ask.place, ResolvedPlace) else None,
            model_filled=resource_attributes,
            schema_context_index=deps.schema_context_index,
            filterable=schema.filterable,
            # The fields the pack declares, which is not the same as the fields it
            # lets a caller filter on: `AgricultureFacility` declares `location`
            # and leaves it out of `filterable_paths`.
            declared=tuple(schema.field_types),
            # TODO(#55): remove with the pack fix.
            #
            # Always today. Nothing reads a date out of the question yet, so a
            # farmer asking about yesterday still gets today's window —
            # `arrivalDate` is filterable and is where that will come from.
            priced_on=deps.today(),
        )

    consulted = await _consult_all(deps, consulting, attributes_for=attributes_for)
    chosen = next((one for one in consulted if one.answers), None)
    _log_consultation(deps, ask_index=ask_index, consulted=consulted, chosen=chosen)

    if chosen is None:
        return _nothing_came_back(deps, ask_index=ask_index, consulted=consulted)

    deps.raw_answers.extend((ask_index, answer) for answer in chosen.answers)
    return render_answers_as_markdown(chosen.answers)


@dataclass
class _Consulted:
    """One provider's outcome for one ask, kept whether or not it was used.

    The unused ones are the point: the team compares them later to judge
    whether the ranking is ordered the way it should be (ADR-0019). They never
    reach the model or the farmer — one ask is answered by one provider.
    """

    capability: ProviderCapability
    answers: tuple[DiscoveredAnswer, ...] = ()
    failure: SelectFailedError | None = None


def _consulting(
    deps: PlannerDeps, *, ask_index: int, capability: ProviderCapability
) -> tuple[ProviderCapability, ...]:
    """The candidates this call will consult, in the order it will use them.

    Restricted to the chosen capability's own @type. The model authored
    `resource_attributes` against one schema and `validate_arguments` checked
    them against it; a candidate of another @type would be sent fields its
    pack never declared. Discovery already ordered what is left by the
    client's ranking, so taking them as they come is taking them in
    preference order.

    One provider can offer several resources for an ask. All of them are
    consulted — the ranking orders providers, and says nothing about which of
    a provider's own resources answers better.
    """

    if priority_for_ask(deps.discovery, ask_index=ask_index) is None:
        return (capability,)
    return tuple(
        candidate
        for candidate in deps.discovery.capabilities.get(ask_index, ())
        if candidate.capability == capability.capability
    )


async def _consult_all(
    deps: PlannerDeps,
    consulting: tuple[ProviderCapability, ...],
    *,
    attributes_for: Callable[[ProviderCapability], dict],
) -> tuple[_Consulted, ...]:
    """Call every candidate, concurrently, and bring back what each said.

    Concurrent rather than one-at-a-time-until-one-answers: the farmer waits
    for the slowest provider either way once more than one has to be asked,
    and asking them together keeps that cost to one provider's latency rather
    than the sum. Each call is already bounded by `DSS_SELECT_TIMEOUT_SECONDS`.

    A provider that fails its call comes back as a `_Consulted` carrying the
    failure, because one unreachable provider must not cost the farmer the
    answer another one had. Only `SelectFailedError` is caught — the adapter's
    "reached the end of retrying" signal. Anything else is a defect or a
    wiring mistake (`_UnwiredInvocation` raises one on purpose), and those
    stay loud: the group cancels and the turn fails rather than reporting a
    broken deployment as a provider with no data.
    """

    outcomes = [_Consulted(capability=candidate) for candidate in consulting]

    async def consult(slot: int) -> None:
        outcome = outcomes[slot]
        try:
            answers = await deps.invocation.select(
                outcome.capability,
                attributes_for(outcome.capability),
                deps.turn.transaction_id,
                visibility=deps.visibility,
            )
        except SelectFailedError as exc:
            # The adapter has already retried and given up.
            outcome.failure = exc
            return
        outcome.answers = tuple(answers)

    if len(outcomes) == 1:
        await consult(0)
        return tuple(outcomes)

    async with anyio.create_task_group() as group:
        for slot in range(len(outcomes)):
            group.start_soon(consult, slot)
    return tuple(outcomes)


def _log_consultation(
    deps: PlannerDeps,
    *,
    ask_index: int,
    consulted: tuple[_Consulted, ...],
    chosen: _Consulted | None,
) -> None:
    """Record what every consulted provider gave back, used or not.

    Counts, ids and error details only — each response body is already logged
    at DEBUG by the invocation adapter, against the same `request_id`, so a
    reader who needs the values joins on that rather than getting them twice
    at INFO (ADR-0014).

    `used` names the one resource the answer was built from — the resource,
    not the provider, because one provider can offer two resources for an ask
    and only one of them answered.
    """

    for position, one in enumerate(consulted):
        log_event(
            "provider_preferences",
            deps.turn.transaction_id,
            event="provider_consulted",
            ask_index=ask_index,
            rank=position,
            provider_id=one.capability.provider_id,
            resource_id=one.capability.resource_id,
            results=len(one.answers),
            failed=one.failure is not None,
            detail=_detail(one.failure),
            used=one is chosen,
        )


def _detail(failure: SelectFailedError | None) -> str:
    if failure is None:
        return "-"
    return str(failure.detail or failure.status_code)


def _nothing_came_back(
    deps: PlannerDeps, *, ask_index: int, consulted: tuple[_Consulted, ...]
) -> str:
    """What the model is told when no consulted provider had an answer.

    A failure is recorded per provider that errored — but only here, on the
    path where the consultation ends with nothing. `Evidence.failed` is what
    makes the composer say "we could not reach Agmarknet", and saying that
    about a lower-ranked provider whose outage cost the farmer nothing would
    be a worse answer than the one they are getting.

    The ask is finished only when no candidate is left unconsulted. Without a
    ranking this call consulted the one candidate the model named, and the
    others it did not name may still answer — the behaviour before any
    preference existed. With one, a ranked provider can serve the same need
    through another `@type`, whose fields this call could not author; picking
    such a candidate is a fresh `select`, not a re-run of this set.
    """

    for one in consulted:
        if one.failure is None:
            continue
        deps.failures.append(
            (
                ask_index,
                Failure(
                    ask_index=ask_index,
                    capability=one.failure.capability,
                    reason=_detail(one.failure),
                    retryable=one.failure.failure_class is FailureClass.TRANSIENT,
                ),
            )
        )

    names = ", ".join(one.capability.provider_name for one in consulted)
    reached = sum(1 for one in consulted if one.failure is None)
    if reached:
        outcome = (
            f"No data for ask {ask_index}: {names} were consulted and none "
            f"had an answer."
        )
    else:
        outcome = f"Could not reach any provider for ask {ask_index} ({names})."
    if _unconsulted(deps, ask_index=ask_index, consulted=consulted):
        return (
            f"{outcome} Try another candidate for ask {ask_index}, or report "
            f"that this ask has no answer."
        )
    return (
        f"{outcome} Do not call select for this ask again; report that it "
        f"has no answer."
    )


def _unconsulted(
    deps: PlannerDeps, *, ask_index: int, consulted: tuple[_Consulted, ...]
) -> bool:
    """Whether the ask still has candidates this call did not reach."""

    tried = {one.capability.resource_id for one in consulted}
    return any(
        candidate.resource_id not in tried
        for candidate in deps.discovery.capabilities.get(ask_index, ())
    )


async def _describe_capability(ctx: RunContext[PlannerDeps], ask_index: int) -> str:
    """Show an ask's candidates and each one's filterable fields, so the
    model knows what it may set before calling ``select``."""

    candidates = ctx.deps.discovery.capabilities.get(ask_index, ())
    return render_candidates_as_markdown(candidates, schemas=ctx.deps.schemas)


_TOOLS = {"select": _select, "describe_capability": _describe_capability}


def build_planner_agent(
    *,
    skills: Sequence[Skill],
    model: Model | str | None = None,
    system_prompt: str = "",
    retries: int = 1,
) -> Agent[PlannerDeps, str]:
    """Construct the planner agent, binding the union of the selected skills'
    ``tool_names`` (ADR-0006). An unselected skill's tools are never in the
    model's schema.

    A skill naming a tool that does not exist raises here rather than binding
    what it can: its guidance would tell the model to call a tool that is not
    there. Same stance as ``load_skills`` — do not boot on a broken config.

    ``name=name`` is required. Without it the tool registers under the Python
    function's own name (``_select``), and the model's call for ``select``
    comes back "Unknown tool name".

    ``model`` and ``system_prompt`` default to unset so a test can drive the
    agent with ``agent.override(model=...)`` and no prompt.

    ``retries`` is the model's own budget for ``ModelRetry``. The design
    raises it in three places — an invented field, an unknown ``resource_id``,
    a capability with no loaded schema — so the framework's default of 1 is
    too few for a real turn.
    """

    agent: Agent[PlannerDeps, str] = Agent(
        model,
        name="planner",
        deps_type=PlannerDeps,
        system_prompt=system_prompt,
        retries=retries,
    )
    for name in tool_names_for(skills):
        agent.tool(_TOOLS[name], name=name)
    return agent
