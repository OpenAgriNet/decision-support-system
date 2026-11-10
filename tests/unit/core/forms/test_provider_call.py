from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dss.core.forms.service import (
    build_action_attributes,
    candidates_for_scheme,
    discovery_for_scheme,
    without_farmer_values,
)
from dss.core.provider_discovery.models import DiscoveryResult
from tests.support.grievance_pack import (
    PMFBY_STATUS,
    PMFBY_SUPPORT,
    PMKISAN_INIT,
    PMKISAN_SUPPORT,
    find_action_schema,
)
from tests.support.providers import build_capability, grievance_provider


def test_only_the_providers_advertising_the_scheme_are_kept() -> None:
    pmfby = grievance_provider("pmfby-provider", "PMFBY")
    kisan = grievance_provider("kisan-provider", "PM-KISAN")
    unmarked = build_capability(
        provider_id="plain",
        provider_name="PLAIN",
        capability="openagrinet:Grievance",
        resource_id="res:plain:grievance",
    )

    kept = candidates_for_scheme((kisan, pmfby, unmarked), "PMFBY")

    assert kept == (pmfby,)


def test_discovery_for_a_scheme_keeps_each_asks_providers_of_that_scheme() -> None:
    pmfby = grievance_provider("pmfby-provider", "PMFBY")
    kisan = grievance_provider("kisan-provider", "PM-KISAN")
    found = DiscoveryResult(
        answers={},
        capabilities={0: (kisan, pmfby), 1: (kisan,)},
        failures={},
        events=(),
    )

    narrowed = discovery_for_scheme(found, "PMFBY")

    assert narrowed == replace(found, capabilities={0: (pmfby,), 1: ()})


_FILE_GRIEVANCE_EXAMPLE = json.loads(
    (
        Path(__file__).parents[3]
        / "fixtures"
        / "api_packs"
        / "pmfby_file_grievance_example.json"
    ).read_text(encoding="utf-8")
)


def test_the_body_holds_every_must_send_path_from_pack_provider_and_form() -> None:
    """Compared with the pack's published example, less the `maySend` fields
    this slice does not send."""

    schema = find_action_schema(PMFBY_SUPPORT)
    provider = build_capability(
        provider_id="pmfby",
        provider_name="PMFBY Grievance Portal",
        capability="openagrinet:Grievance",
        resource_id="res:pmfby:grievance",
    )
    values = {
        "enrolmentId": "KA2026KH00123456",
        "applicantMobile": "9876543210",
        "cropYear": "2026",
        "season": "Kharif",
        "grievance.description": (
            "Cannot log in to the PMFBY portal to view my Kharif 2026 enrolment."
        ),
    }

    body = build_action_attributes(schema, provider, values)

    expected = {
        key: value
        for key, value in _FILE_GRIEVANCE_EXAMPLE.items()
        if key not in ("complaintDate", "receiptSourceId")
    }
    expected["grievance"] = {
        "description": _FILE_GRIEVANCE_EXAMPLE["grievance"]["description"]
    }
    expected["scheme"] = {"code": "PMFBY"}
    assert body == expected


_READ_CASE_EXAMPLE = json.loads(
    (
        Path(__file__).parents[3]
        / "fixtures"
        / "api_packs"
        / "pmfby_read_case_example.json"
    ).read_text(encoding="utf-8")
)


def test_a_status_body_matches_the_packs_read_case_example() -> None:
    """The same builder reads a case: the pack's example, less the scheme's
    name, a `maySend` field this slice does not send."""

    schema = find_action_schema(PMFBY_STATUS)
    provider = build_capability(
        provider_id="pmfby",
        provider_name="PMFBY Grievance Portal",
        capability="openagrinet:Grievance",
        resource_id="res:pmfby:grievance",
    )
    values = {"applicantMobile": "9876543210", "case.ticketNo": "100626000099001"}

    body = build_action_attributes(schema, provider, values)

    expected = dict(_READ_CASE_EXAMPLE)
    expected["scheme"] = {"code": "PMFBY"}
    assert body == expected


def test_the_reply_keeps_what_the_provider_added_not_what_the_farmer_typed() -> None:
    schema = find_action_schema(PMFBY_SUPPORT)
    reply = {
        "@type": "openagrinet:Grievance",
        "scheme": {"code": "PMFBY"},
        "enrolmentId": "KA2026KH00123456",
        "applicantMobile": "9876543210",
        "grievance": {"description": "No payment yet.", "category": {"code": "3"}},
        "case": {"ticketNo": "100626000099001"},
    }

    kept = without_farmer_values(reply, schema)

    assert kept == {
        "@type": "openagrinet:Grievance",
        "scheme": {"code": "PMFBY"},
        "grievance": {"category": {"code": "3"}},
        "case": {"ticketNo": "100626000099001"},
    }


def test_the_reply_drops_what_the_farmer_typed_after_init() -> None:
    reply = {
        "challenge": {"response": {"method": "OTP", "value": "4821"}},
        "case": {"ticketNo": "100626000099001"},
    }

    kept = without_farmer_values(reply, find_action_schema(PMKISAN_SUPPORT))

    assert kept == {
        "challenge": {"response": {"method": "OTP"}},
        "case": {"ticketNo": "100626000099001"},
    }


_PMKISAN_DETAILS = {
    "enrolmentId": "PMK2026000123",
    "grievance.category.code": "G003",
    "grievance.description": "My last instalment was not credited to my account.",
}
_PMKISAN_PROVIDER = grievance_provider("pmkisan", "PM-KISAN")


def test_init_takes_only_its_own_paths_from_the_details_form() -> None:
    init = find_action_schema(PMKISAN_INIT)

    body = build_action_attributes(init, _PMKISAN_PROVIDER, _PMKISAN_DETAILS)

    assert {key: value for key, value in body.items() if not key.startswith("@")} == {
        "informationMode": "OnDemand",
        "scheme": {"code": "PM-KISAN"},
        "enrolmentId": "PMK2026000123",
    }


def test_the_init_values_fill_the_path_that_waits_for_init() -> None:
    body = build_action_attributes(
        find_action_schema(PMKISAN_SUPPORT),
        _PMKISAN_PROVIDER,
        _PMKISAN_DETAILS,
        init_values={"challenge.response.value": "4821"},
    )

    assert body["challenge"] == {"response": {"method": "OTP", "value": "4821"}}
    assert body["enrolmentId"] == "PMK2026000123"


def test_a_value_on_the_details_form_never_answers_init() -> None:
    body = build_action_attributes(
        find_action_schema(PMKISAN_SUPPORT),
        _PMKISAN_PROVIDER,
        {**_PMKISAN_DETAILS, "challenge.response.value": "0000"},
        init_values={"challenge.response.value": "4821"},
    )

    assert body["challenge"]["response"]["value"] == "4821"


def test_an_action_needing_init_without_its_values_is_refused() -> None:
    with pytest.raises(ValueError, match="needs the init values"):
        build_action_attributes(
            find_action_schema(PMKISAN_SUPPORT), _PMKISAN_PROVIDER, _PMKISAN_DETAILS
        )


def test_init_values_for_an_action_needing_none_are_refused() -> None:
    with pytest.raises(ValueError, match="needs no init values"):
        build_action_attributes(
            find_action_schema(PMKISAN_INIT),
            _PMKISAN_PROVIDER,
            _PMKISAN_DETAILS,
            init_values={"challenge.response.value": "4821"},
        )
