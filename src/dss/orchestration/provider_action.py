from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from dss.core.forms.models import ActionReply, ActionSchema
from dss.core.forms.service import build_action_attributes, without_farmer_values
from dss.core.intent.models import AskId
from dss.core.planner.models import Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.redaction.visibility import Visibility
from dss.core.shared.models import ProviderAction
from dss.core.shared.network import NetworkTransactionID
from dss.ports.invocation import ProviderActionInvocation, ProviderCallFailedError

logger = logging.getLogger(__name__)

# A form turn has one ask: the form's.
FORM_ASK_ID = AskId(0)

type InvocationCall = Callable[..., Awaitable[ActionReply]]


async def call_provider_action(  # noqa: PLR0913
    provider: ProviderCapability,
    action: ActionSchema,
    values: Mapping[str, str],
    *,
    invocation: ProviderActionInvocation,
    transaction_id: NetworkTransactionID,
    visibility: Visibility,
    init_values: Mapping[str, str] | None = None,
) -> DiscoveredAnswer | Failure:
    invocation_call = _invocation_call_for(action, invocation)
    body = build_action_attributes(action, provider, values, init_values=init_values)
    try:
        reply = await invocation_call(
            provider, body, transaction_id, visibility=visibility
        )
    except ProviderCallFailedError as failed:
        return _as_failure(failed)
    return _as_answer(provider, reply, action)


def _invocation_call_for(
    action: ActionSchema, invocation: ProviderActionInvocation
) -> InvocationCall:
    calls_by_action: Mapping[ProviderAction, InvocationCall] = {
        ProviderAction.INIT: invocation.init,
        ProviderAction.SUPPORT: invocation.support,
        ProviderAction.STATUS: invocation.status,
    }
    return calls_by_action[action.context.action]


def _as_failure(failed: ProviderCallFailedError) -> Failure:
    return Failure(
        ask_id=FORM_ASK_ID,
        capability=failed.capability,
        kind=failed.kind,
        reason=failed.reason,
        retry_after_seconds=failed.retry_after_seconds,
    )


def _as_answer(
    provider: ProviderCapability, reply: ActionReply, action: ActionSchema
) -> DiscoveredAnswer:
    # The provider echoes the farmer's values; no typed value may reach the composer.
    return DiscoveredAnswer(
        provider_id=provider.provider_id,
        provider_name=provider.provider_name,
        capability=provider.capability,
        resource_id=provider.resource_id,
        attributes=without_farmer_values(reply.attributes, action),
        validity=None,
    )


# Never log the reason: it is the provider's body.
def warn_auth_refused(failure: Failure, *, request_id: str) -> None:
    logger.warning(
        "provider_auth_refused",
        extra={"request_id": request_id, "capability": failure.capability},
    )
