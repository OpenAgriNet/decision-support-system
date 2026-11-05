"""The form step: which form an ask names, the offer a turn ends with, and
the ask a submitted form stands for.

Here and not in `core/forms`, so forms never imports intent: the mapping
between an ask and a form's context is the paths' knowledge, and both
directions live together so they cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from dss.core.forms.models import FormContext, FormDefinition, FormPiece
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import missing_pieces
from dss.core.intent.models import Ask, Intent, InteractionType, Scheme, SubjectCategory
from dss.core.provider_discovery.models import DiscoveryResult

# The pack's call each interaction leads to. Advice leads to the filing it is
# advice about: asking how to file comes with the file form.
_ACTION_BY_INTERACTION: Mapping[InteractionType, str] = {
    InteractionType.ACT: "support",
    InteractionType.OBSERVE: "status",
    InteractionType.ADVISE: "support",
}
# Written out, not inverted from the table above: advise maps to `support` too,
# and a submitted `support` form is always a filing.
_INTERACTION_BY_ACTION: Mapping[str, InteractionType] = {
    "support": InteractionType.ACT,
    "status": InteractionType.OBSERVE,
}


@dataclass(frozen=True)
class FormStep:
    """What the form step decided for a turn's asks, by position.

    ``settled`` holds the asks the planner must not run for, because the form
    or the question already answers them. Always a subset of the asks offered
    or missing."""

    offered: Mapping[int, FormDefinition] = field(default_factory=dict)
    missing: Mapping[int, tuple[FormPiece, ...]] = field(default_factory=dict)
    settled: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if not self.settled <= self.asks:
            raise ValueError("settled names an ask the step did not take")

    @property
    def asks(self) -> frozenset[int]:
        return frozenset(self.offered) | frozenset(self.missing)


NO_FORM_STEP = FormStep()


def ask_for_form(context: FormContext, *, scheme: Scheme) -> Ask | None:
    """The one ask a submission runs discovery for, or ``None`` for a context
    no ask maps to: the registry serves every action a pack declares, and
    only `support` and `status` can be filed. No place: the form carries what its
    call needs."""

    interaction = _INTERACTION_BY_ACTION.get(context.action)
    if interaction is None or context.category not in SubjectCategory:
        return None
    return Ask(
        subject_categories=SubjectCategory(context.category),
        interaction_type=interaction,
        scheme=scheme,
    )


def form_context_for(ask: Ask) -> FormContext | None:
    """The form's type when the ask says every piece of it."""

    known = _known_pieces(ask)
    if known.keys() < FormContext.model_fields.keys():
        return None
    return FormContext(**known)


def _known_pieces(ask: Ask) -> dict[FormPiece, str]:
    """The pieces of a form's type the ask already says. The one place that
    maps an ask onto a context."""

    pieces = {
        "category": ask.subject_categories.value,
        "action": _ACTION_BY_INTERACTION[ask.interaction_type],
    }
    return pieces | ({"scheme": ask.scheme.code} if ask.scheme else {})


def find_forms(
    intent: Intent, discovery: DiscoveryResult, forms: FormLookup
) -> FormStep:
    """Per ask: the form it names, the pieces it still needs to name one, or
    neither, which leaves it to the planner as today.

    An ask whose own call the form is (filing, checking) is a form ask only
    when a provider serves it, and the planner never runs for it. Advice is
    not: the form is for the filing that follows, so the registry alone offers
    it, or asks for its missing piece, and the planner still answers the advice
    when a provider serves it."""

    form_asks = {
        position: ask
        for position, ask in enumerate(intent.asks)
        if not _is_own_call(ask) or discovery.serves(position)
    }
    offered = {
        position: form
        for position, ask in form_asks.items()
        if (form := _form_for(ask, forms)) is not None
    }
    missing = {
        position: pieces
        for position, ask in form_asks.items()
        if position not in offered and (pieces := _pieces_missing(ask, forms))
    }
    settled = frozenset(
        position
        for position in offered.keys() | missing.keys()
        if _is_own_call(intent.asks[position]) or not discovery.serves(position)
    )
    return FormStep(offered=offered, missing=missing, settled=settled)


def _is_own_call(ask: Ask) -> bool:
    """Whether the form is this very ask's call: filing for an act, checking
    for an observe. Not for advice, whose form is for what follows."""

    action = _ACTION_BY_INTERACTION[ask.interaction_type]
    return _INTERACTION_BY_ACTION[action] is ask.interaction_type


def _form_for(ask: Ask, forms: FormLookup) -> FormDefinition | None:
    context = form_context_for(ask)
    return forms.find(context) if context else None


def _pieces_missing(ask: Ask, forms: FormLookup) -> tuple[FormPiece, ...]:
    return missing_pieces(
        _known_pieces(ask), forms.contexts_for(ask.subject_categories.value)
    )
