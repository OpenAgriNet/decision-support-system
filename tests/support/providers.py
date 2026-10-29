"""Providers as discovery returns them, for tests that need one by hand."""

from __future__ import annotations

from dss.core.provider_discovery.models import ProviderCapability


def grievance_provider(provider_id: str, scheme_code: str) -> ProviderCapability:
    """A grievance desk advertising one scheme, as the catalog publishes it."""

    return ProviderCapability(
        provider_id=provider_id,
        provider_name=provider_id.upper(),
        capability="openagrinet:Grievance",
        resource_id=f"res:{provider_id}:grievance",
        observed_categories=("Grievance",),
        advertised={"scheme": {"code": scheme_code, "name": scheme_code}},
    )
