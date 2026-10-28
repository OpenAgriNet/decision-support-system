from __future__ import annotations

from uuid import uuid4

from dss.core.forms.models import FormBlock, FormDefinition


def offer_form(definition: FormDefinition) -> FormBlock:
    """A new offer each call: two offers of one form type are two forms to
    fill in, and a submission names the offer it answers."""

    return FormBlock(form_id=uuid4().hex, form=definition)
