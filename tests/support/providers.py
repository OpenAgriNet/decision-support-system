"""Providers as discovery returns them, for tests that need one by hand."""

from __future__ import annotations

from dss.core.provider_discovery.models import ProviderCapability

_DEFAULTS = {
    "provider_id": "mausamgram",
    "provider_name": "IMD",
    "capability": "openagrinet:WeatherObservation",
    "resource_id": "res:mausamgram:point-forecast",
    "bpp_id": "bpp",
    "bpp_uri": "https://bpp.example",
}


def build_capability(**overrides) -> ProviderCapability:
    """A capability with every required field filled. A test names only the
    fields it reads, so a new required field is one line here, not one per
    test."""

    return ProviderCapability(**{**_DEFAULTS, **overrides})


def grievance_provider(provider_id: str, scheme_code: str) -> ProviderCapability:
    """A grievance desk advertising one scheme, as the catalog publishes it."""

    return build_capability(
        provider_id=provider_id,
        provider_name=provider_id.upper(),
        capability="openagrinet:Grievance",
        resource_id=f"res:{provider_id}:grievance",
        observed_categories=("Grievance",),
        advertised={"scheme": {"code": scheme_code, "name": scheme_code}},
    )
