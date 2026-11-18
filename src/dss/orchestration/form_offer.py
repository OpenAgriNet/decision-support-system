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
from dss.core.intent.models import (
    Ask,
    AskId,
    InteractionType,
    Scheme,
    SubjectCategory,
)
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import (
    ProviderAction,
    RequestedAction,
    as_requested_action,
)
from dss.orchestration.provider_action import FORM_ASK_ID

# Discovery finds a provider by the interaction type, so the ask a submitted
# form stands for needs one.
_INTERACTION_BY_ACTION: Mapping[RequestedAction, InteractionType] = {
    ProviderAction.SUPPORT: InteractionType.ACT,
    ProviderAction.STATUS: InteractionType.OBSERVE,
}


def ask_for_form(context: FormContext, *, scheme: Scheme) -> Ask | None:
    """The one ask a submission runs discovery for, or ``None`` for a context
    no ask maps to: the registry serves every action a pack declares, and
    only the two above can be filed. No place: the form carries what its
    call needs."""

    action = as_requested_action(context.action)
    if action is None or context.category not in SubjectCategory:
        return None
    return Ask(
        id=FORM_ASK_ID,
        subject_categories=SubjectCategory(context.category),
        interaction_type=_INTERACTION_BY_ACTION[action],
        scheme=scheme,
        action=action,
    )


def form_context_for(ask: Ask) -> FormContext | None:
    # A guessed scheme gets no form: it could file against the wrong scheme.
    action = ask.known_action
    if not isinstance(ask.scheme, Scheme) or action is None:
        return None
    return FormContext(
        scheme=ask.scheme.code,
        category=ask.subject_category,
        action=action,
    )


def find_forms(
    discovery: DiscoveryResult, forms: FormLookup
) -> dict[AskId, FormDefinition]:
    """The asks a form answers, by ask id: a provider serves the ask, and
    the registry has a form for what it names. Anything else is the
    planner's, as today."""

    form_by_served_ask = {
        discovered.ask.id: _form_for(discovered.ask, forms)
        for discovered in discovery.asks
        if discovered.is_served
    }
    return {
        ask_id: form for ask_id, form in form_by_served_ask.items() if form is not None
    }


def _form_for(ask: Ask, forms: FormLookup) -> FormDefinition | None:
    context = form_context_for(ask)
    return forms.find(context) if context else None
