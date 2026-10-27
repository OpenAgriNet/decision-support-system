"""The form step: which form an ask names, and the offer a turn ends with.

Here and not in `core/forms`, so forms never imports intent: the mapping from
an ask to a form's context is the text path's knowledge.
"""

from __future__ import annotations

from collections.abc import Mapping

from dss.core.forms.models import FormContext
from dss.core.intent.models import Ask, InteractionType

# The pack's calls a form stands for. Advice is answered, never filed.
_ACTION_BY_INTERACTION: Mapping[InteractionType, str] = {
    InteractionType.ACT: "support",
    InteractionType.OBSERVE: "status",
}


def form_context_for(ask: Ask) -> FormContext | None:
    action = _ACTION_BY_INTERACTION.get(ask.interaction_type)
    if ask.scheme is None or action is None:
        return None
    return FormContext(
        scheme=ask.scheme.code, category=ask.subject_categories.value, action=action
    )
