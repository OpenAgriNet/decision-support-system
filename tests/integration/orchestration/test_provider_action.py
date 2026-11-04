from __future__ import annotations

from dss.core.forms.models import (
    ActionField,
    ActionReply,
    ActionSchema,
    FieldSource,
    FormContext,
)
from dss.core.planner.models import Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, FailureClass
from dss.core.redaction.visibility import NOTHING_HELD
from dss.orchestration.provider_action import call_provider_action
from dss.ports.invocation import StatusFailedError, SupportFailedError
from tests.support.providers import grievance_provider

PROVIDER = grievance_provider("pmfby-provider", "PMFBY")

PHONE = "9876543210"
VALUES = {"applicantMobile": PHONE, "grievance.description": "No payment yet."}


def _action(action: str) -> ActionSchema:
    return ActionSchema(
        context=FormContext(scheme="PMFBY", category="Grievance", action=action),
        must_send=(
            ActionField(
                path="scheme.code", source=FieldSource.FIXED, allowed_values=("PMFBY",)
            ),
            ActionField(path="provider.id", source=FieldSource.PROVIDER),
            ActionField(
                path="applicantMobile", source=FieldSource.FARMER, pii_class="contact"
            ),
            ActionField(
                path="grievance.description",
                source=FieldSource.FARMER,
                pii_class="freetext",
            ),
        ),
        jsonld={"@type": "openagrinet:Grievance"},
    )


SUPPORT = _action("support")
STATUS = _action("status")

FILED = {
    "applicantMobile": PHONE,
    "grievance": {"description": "No payment yet."},
    "case": {"ticketNo": "100626000099001"},
}


class _RecordingInvocation:
    def __init__(self, failure: Exception | None = None) -> None:
        self._failure = failure
        self.calls: list[tuple[str, dict]] = []

    async def support(self, capability, attributes, transaction_id, *, visibility):
        return self._answer("support", attributes)

    async def status(self, capability, attributes, transaction_id, *, visibility):
        return self._answer("status", attributes)

    def _answer(self, action: str, attributes: dict) -> ActionReply:
        self.calls.append((action, attributes))
        if self._failure is not None:
            raise self._failure
        return ActionReply(attributes=FILED)


async def _call(action: ActionSchema, invocation: _RecordingInvocation):
    return await call_provider_action(
        PROVIDER,
        action,
        VALUES,
        invocation=invocation,
        transaction_id="t1",
        visibility=NOTHING_HELD,
    )


async def test_the_body_holds_the_forms_values_and_the_provider() -> None:
    invocation = _RecordingInvocation()

    await _call(SUPPORT, invocation)

    ((action, body),) = invocation.calls
    assert action == "support"
    assert body == {
        "@type": "openagrinet:Grievance",
        "scheme": {"code": "PMFBY"},
        "provider": {"id": "pmfby-provider"},
        "applicantMobile": PHONE,
        "grievance": {"description": "No payment yet."},
    }


async def test_a_status_form_is_sent_with_status() -> None:
    invocation = _RecordingInvocation()

    await _call(STATUS, invocation)

    ((action, _body),) = invocation.calls
    assert action == "status"


async def test_the_reply_loses_the_farmers_values() -> None:
    response = await _call(SUPPORT, _RecordingInvocation())

    assert isinstance(response, DiscoveredAnswer)
    assert response.provider_id == "pmfby-provider"
    assert response.attributes == {"case": {"ticketNo": "100626000099001"}}


async def test_a_failed_call_becomes_a_failure() -> None:
    refused = SupportFailedError(
        "openagrinet:Grievance",
        503,
        FailureClass.TRANSIENT,
        retry_after_seconds=120,
    )

    response = await _call(SUPPORT, _RecordingInvocation(refused))

    assert response == Failure(
        ask_index=0,
        capability="openagrinet:Grievance",
        reason="support failed with status 503",
        retryable=True,
        retry_after_seconds=120,
    )


async def test_a_refused_status_is_not_retryable() -> None:
    refused = StatusFailedError("openagrinet:Grievance", 404, FailureClass.DEFECT)

    response = await _call(STATUS, _RecordingInvocation(refused))

    assert isinstance(response, Failure)
    assert response.retryable is False
    assert response.reason == "status failed with status 404"
