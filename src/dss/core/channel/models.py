"""What composition produces."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from dss.core.shared.models import OutputBlock, Source


class ComposedAnswer(BaseModel):
    """The written answer and the sources behind it.

    Every `source_ids` entry on a block must name a source listed here — a
    citation marker pointing at nothing is worse than no marker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: tuple[OutputBlock, ...]
    sources: tuple[Source, ...] = ()


class ClarificationText(BaseModel):
    """Fixed text a farmer reads that the composer does not write. Loaded from
    config, like `Identity`. Some lines are `str.format` templates, such as
    `no_provider_for`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Closes a pick list of places that was cut at `max_choices`.
    more_places_hint: str
    # Nobody on the network serves the ask. A device point can have no name,
    # so the second one names no place.
    no_provider_for: str
    no_provider: str
    # A submitted form the DSS cannot accept (#205). The field messages are
    # `str.format` templates over `{label}`, `{min}` and `{max}`.
    unknown_form: str
    form_has_errors: str
    field_required: str
    field_pattern: str
    field_min_length: str
    field_max_length: str
    field_not_an_option: str
    field_duplicate: str
    field_unknown: str
    # A filing that did not reach the desk, and one the desk refused.
    not_filed: str
    not_filed_rejected: str
    # A status check that did not reach the desk, and one the desk refused:
    # read as no case for that ticket and phone (#206).
    status_not_checked: str
    status_not_found: str
    # A form turn while another from the same farmer still holds the session.
    session_busy: str
    # A form sent again after the provider took it.
    already_filed: str
    # An init response with no form waiting for it: never sent, expired or used.
    no_init_pending: str
    # Init did not reach the provider, or the provider refused the details.
    init_not_sent: str
    init_rejected: str
    # Sent with the init form: what the farmer answers, e.g. an OTP.
    init_response_needed: str
    # Our own fault, e.g. a pack that gives no init for a scheme that needs one.
    went_wrong: str
    # A form that needs init, on a session nothing keeps past this turn.
    form_cannot_complete: str
    # Saved details the DSS can no longer use, e.g. the pack changed between turns.
    saved_details_unusable: str
    # The field error on an init response handed back; the sentence says why.
    field_recheck: str
    # The provider saw the init response and refused it; or never took it in.
    init_response_refused: str
    init_response_not_sent: str
    # It may have reached the provider: the farmer checks before sending again.
    outcome_unknown: str
    # A status check with an init response the provider refused: no such case.
    case_not_found: str
    # More choices than this are grouped one level up before they are listed.
    max_choices: int = Field(default=5, gt=0)

    @field_validator(
        "field_required",
        "field_pattern",
        "field_min_length",
        "field_max_length",
        "field_not_an_option",
        "field_duplicate",
        "field_unknown",
    )
    @classmethod
    def _renders_with_the_known_placeholders(
        cls, template: str, info: ValidationInfo
    ) -> str:
        """An adopter's typo stops the boot, not a farmer's submission."""

        try:
            template.format(label="", min=0, max=0)
        except (KeyError, IndexError, ValueError) as exc:
            allowed = "{label}, {min} and {max}"
            raise ValueError(
                f"{info.field_name}: only {allowed} may appear: {exc}"
            ) from exc
        return template
