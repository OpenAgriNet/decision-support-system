"""Which facts an ask needs, worked out from plain data about its category.
The caller fetches that data, so settlement reads no pack and no registry."""

from __future__ import annotations

from collections.abc import Iterable

from dss.core.forms.models import FormContext
from dss.core.intent.models import Ask, UnclearAction
from dss.core.settlement.models import AskNeeds
from dss.core.shared.models import ProviderAction


def build_ask_needs(
    ask: Ask, *, needs_place: bool, forms: Iterable[FormContext]
) -> AskNeeds:
    """`forms` are every form of the ask's category."""

    fileable = _fileable_forms(ask, forms)
    # Until the action is known, a form for any action counts: the farmer's
    # answer may still pick the one a scheme has.
    is_open = ask.action is UnclearAction.UNCLEAR
    counted = [form for form in fileable if is_open or form.action == ask.action]
    return AskNeeds(
        needs_place=needs_place,
        schemes_with_a_form=frozenset(form.scheme for form in counted),
        actions_with_a_form=frozenset(ProviderAction(form.action) for form in fileable),
    )


def _fileable_forms(ask: Ask, forms: Iterable[FormContext]) -> list[FormContext]:
    # An ask with no action wants an answer, not a form.
    if ask.action is None:
        return []
    # A pack can declare a step, such as init, that no form files.
    return [form for form in forms if form.action in ProviderAction]
