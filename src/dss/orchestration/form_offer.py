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
from dss.core.shared.models import ProviderAction

# Discovery finds a provider by the interaction type, so the ask a submitted
# form stands for needs one.
_INTERACTION_BY_ACTION: Mapping[ProviderAction, InteractionType] = {
    ProviderAction.SUPPORT: InteractionType.ACT,
    ProviderAction.STATUS: InteractionType.OBSERVE,
}


def ask_for_form(context: FormContext, *, scheme: Scheme) -> Ask | None:
    """The one ask a submission runs discovery for, or ``None`` for a context
    no ask maps to: the registry serves every action a pack declares, and
    only the two above can be filed. No place: the form carries what its
    call needs."""

    if context.action not in ProviderAction or context.category not in SubjectCategory:
        return None
    action = ProviderAction(context.action)
    return Ask(
        subject_categories=SubjectCategory(context.category),
        interaction_type=_INTERACTION_BY_ACTION[action],
        scheme=scheme,
        action=action,
    )


def form_context_for(ask: Ask) -> FormContext | None:
    # A guessed scheme gets no form: it could file against the wrong scheme.
    if not isinstance(ask.scheme, Scheme):
        return None
    if not isinstance(ask.action, ProviderAction):
        return None
    return FormContext(
        scheme=ask.scheme.code,
        category=ask.subject_categories.value,
        action=ask.action,
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
