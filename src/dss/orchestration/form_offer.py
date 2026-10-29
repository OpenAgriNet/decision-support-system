"""The form step: which form an ask names, the offer a turn ends with, and
the ask a submitted form stands for.

Here and not in `core/forms`, so forms never imports intent: the mapping
between an ask and a form's context is the paths' knowledge, and both
directions live together so they cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping

from dss.core.forms.models import FormContext, FormDefinition
from dss.core.forms.registry import FormLookup
from dss.core.intent.models import Ask, Intent, InteractionType, Scheme, SubjectCategory
from dss.core.provider_discovery.models import DiscoveryResult

# The pack's calls a form stands for. Advice is answered, never filed.
_ACTION_BY_INTERACTION: Mapping[InteractionType, str] = {
    InteractionType.ACT: "support",
    InteractionType.OBSERVE: "status",
}
_INTERACTION_BY_ACTION: Mapping[str, InteractionType] = {
    action: interaction for interaction, action in _ACTION_BY_INTERACTION.items()
}


def ask_for_form(context: FormContext, *, scheme: Scheme) -> Ask | None:
    """The one ask a submission runs discovery for, or ``None`` for a context
    no ask maps to: the registry serves every action a pack declares, and
    only the two above can be filed. No place: the form carries what its
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
    action = _ACTION_BY_INTERACTION.get(ask.interaction_type)
    if ask.scheme is None or action is None:
        return None
    return FormContext(
        scheme=ask.scheme.code, category=ask.subject_categories.value, action=action
    )


def find_forms(
    intent: Intent, discovery: DiscoveryResult, forms: FormLookup
) -> dict[int, FormDefinition]:
    """The asks a form answers, by position: a provider serves the ask, and
    the registry has a form for what it names. Anything else is the
    planner's, as today."""

    return {
        position: form
        for position, ask in enumerate(intent.asks)
        if discovery.serves(position) and (form := _form_for(ask, forms)) is not None
    }


def _form_for(ask: Ask, forms: FormLookup) -> FormDefinition | None:
    context = form_context_for(ask)
    return forms.find(context) if context else None
