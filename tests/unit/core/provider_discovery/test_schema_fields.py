"""Tier 1 — one flat list of a pack's fields.

A pack does not list its fields in one place. It says "my fields are whatever
`AgricultureResource` has, plus mine", using `allOf` and a `$ref`. So knowing
every field means opening two files and merging them.

This flattens that into one map: field path → what the field is. Both wire
calls need it — `/discover` reads a field's allowed values to build a filter,
`/select` reads names and types to build a body.

Pure: yaml text in, dict out. The adapter reads the files.

Fixtures here are small and hand-written, so each test asserts the *whole*
result. A partial assertion passes while the merge quietly drops a field.
"""

from __future__ import annotations

from pathlib import Path

from dss.core.provider_discovery.schema_fields import FieldSpec, flatten_fields

# Untrimmed copies of two real packs. The hand-written fixtures below prove the
# merge rules; this proves those rules survive contact with a pack nobody here
# wrote. It caught a live bug: the ref chain is three hops
# (MandiPrice → AgricultureResource → AgricultureResourceFields →
# AgricultureSubjectReference) and the last hop was being resolved against the
# pack's document instead of the shared one, raising `KeyError`.
#
# Deliberately not the fixture under `tests/integration/adapters/schema_packs/`
# — that one is trimmed to what the capability index reads, so it carries none
# of MandiPrice's own fields and would prove nothing here.
REAL_PACKS = Path(__file__).parent / "fixtures" / "real-packs"

_ONE_FILE = """
components:
  schemas:
    Sample:
      type: object
      x-jsonld:
        "@type": openagrinet:Sample
      allOf:
        - type: object
          required: [informationMode]
          properties:
            informationMode:
              type: string
              enum: [OnDemand, Direct]
            variety:
              type: string
"""


def test_fields_under_all_of_come_out_flat() -> None:
    """`allOf` disappears; its members' fields land in one map.

    Keyed by path, because that is what both call sites hold: the planner
    names a field to fill, and the discover filter names one to match on.
    """

    fields = flatten_fields(_ONE_FILE, pack_name="Sample", shared_yaml=None)

    assert fields == {
        "informationMode": FieldSpec(
            type="string", required=True, enum=("OnDemand", "Direct")
        ),
        "variety": FieldSpec(type="string", required=False, enum=()),
    }


_SHARED = """
components:
  schemas:
    AgricultureResourceFields:
      type: object
      required: [informationMode]
      properties:
        informationMode:
          type: string
          enum: [OnDemand, Direct]
        subjectCategories:
          type: array
          items:
            type: string
            enum: [Crop, Weather, Market]
        location:
          $ref: "https://schema.beckn.io/Location/v2.0/attributes.yaml#/components/schemas/Location"
"""

_TWO_FILES = """
components:
  schemas:
    Sample:
      type: object
      allOf:
        - $ref: "../../AgricultureResource/v0.1/attributes.yaml#/components/schemas/AgricultureResourceFields"
        - type: object
          required: ["@type"]
          properties:
            "@type":
              const: openagrinet:Sample
            variety:
              type: string
"""


def test_the_shared_fields_are_merged_in_with_the_packs_own() -> None:
    """The real shape: `allOf` pulls in AgricultureResource, then adds fields.

    Three things this pins, all of which the wire depends on:

    - an inherited field (`informationMode`) sits beside an own field
      (`variety`), with no trace of which file it came from;
    - `subjectCategories` reports the enum from `items`, not from itself —
      `/discover` filters on those values, so reading the array's own (absent)
      enum would yield nothing to filter by;
    - a Beckn `https://` ref stops at the boundary as a plain object. Not
      resolved, because that would make flattening need the network. The DSS
      copies `location` from the turn's geometry, so its inner shape is
      already known in code.
    """

    fields = flatten_fields(_TWO_FILES, pack_name="Sample", shared_yaml=_SHARED)

    assert fields == {
        "informationMode": FieldSpec(
            type="string", required=True, enum=("OnDemand", "Direct")
        ),
        "subjectCategories": FieldSpec(
            type="array<string>", required=False, enum=("Crop", "Weather", "Market")
        ),
        "location": FieldSpec(type="object", required=False, enum=()),
        "@type": FieldSpec(type="constant", required=True, enum=()),
        "variety": FieldSpec(type="string", required=False, enum=()),
    }


def _real(pack: str) -> str:
    return (REAL_PACKS / f"{pack}.attributes.yaml").read_text(encoding="utf-8")


def test_a_real_pack_flattens_to_the_fields_both_wire_calls_need() -> None:
    """MandiPrice against the real AgricultureResource it `$ref`s.

    Asserted whole, so a field appearing or vanishing shows up. The values are
    what the two wire calls read:

    - `subjectCategories`' enum is what a `/discover` filter matches on;
    - `supportedPriceFields` and `commodity` are what a `/select` body carries.

    Nested paths (`prices.modal`, `market.state`) are deliberately absent —
    only top-level fields are listed. `profile.json`'s `filterable_paths`
    already names the nested ones, so nothing needs them from here yet.
    """

    fields = flatten_fields(
        _real("MandiPrice"),
        pack_name="MandiPrice",
        shared_yaml=_real("AgricultureResource"),
    )

    inherited = {
        "informationMode": FieldSpec(
            type="string", required=True, enum=("OnDemand", "Direct")
        ),
        # Required, and `Facility` is in the enum — both landed in
        # network-specs `eb777cd` ("align shared agriculture subject
        # categories"). A `/discover` filter matches on these values, so the
        # list is the wire contract, not decoration.
        "subjectCategories": FieldSpec(
            type="array<string>",
            required=True,
            enum=(
                "Crop",
                "Livestock",
                "Weather",
                "Market",
                "Scheme",
                "Practice",
                "Facility",
            ),
        ),
        "agricultureSubjects": FieldSpec(type="array<object>", required=False),
        "languages": FieldSpec(type="array<string>", required=False),
        "coverageAreas": FieldSpec(type="array<choice>", required=False),
    }
    own = {
        "@type": FieldSpec(type="choice", required=True),
        "supportedPriceFields": FieldSpec(
            type="array<string>",
            required=False,
            enum=("Minimum", "Maximum", "Modal"),
        ),
        "supportedCommodities": FieldSpec(type="array<object>", required=False),
        "historicalDataAvailable": FieldSpec(type="boolean", required=False),
        # A string field the pack gives a `format`. `type` alone reads the
        # same as free text, and the model wrote "this week" for `arrivalDate`.
        "arrivalDate": FieldSpec(type="string", required=False, format="date"),
        "generatedAt": FieldSpec(type="string", required=False, format="date-time"),
        "historyPeriod": FieldSpec(type="string", required=False, format="duration"),
        "updateFrequency": FieldSpec(type="string", required=False, format="duration"),
        # The rest are optional and carry no enum, so listing them one by one
        # says nothing the type does not. Still asserted, so a field that
        # vanishes or changes type still fails.
        **{
            name: FieldSpec(type="string", required=False)
            for name in (
                "commodityGroup",
                "grade",
                "variety",
            )
        },
        **{
            name: FieldSpec(type="object", required=False)
            for name in ("source", "commodity", "market", "prices", "validity")
        },
    }

    assert fields == inherited | own


def test_a_field_wrapped_in_all_of_resolves_to_what_it_refs() -> None:
    """`facilityType` is `allOf: [$ref: FacilityType]`, and the ref carries
    the type and the allowed values.

    `_fields_of` followed a bare `$ref` but never dissolved an `allOf`, so the
    field came out `object` with no enum. The planner then had nothing saying
    it is a string from a fixed set, wrote the farmer's own words
    ("krishi kendra"), and the provider rejected the select.
    """

    fields = flatten_fields(
        _real("AgricultureFacility"),
        pack_name="AgricultureFacility",
        shared_yaml=_real("AgricultureResource"),
    )

    assert fields["facilityType"] == FieldSpec(
        type="string",
        required=False,
        enum=(
            "CustomHiringCentre",
            "KrishiVigyanKendra",
            "Warehouse",
            "SoilTestingFacility",
        ),
    )


def test_a_field_records_the_format_the_pack_declares() -> None:
    """`type` alone does not say enough. `arrivalDate` is a `string` like 85
    other fields, and `format: date` is the half that says an ISO date rather
    than free text — the model wrote "this week" and the provider refused it.
    """

    fields = flatten_fields(
        """
components:
  schemas:
    MandiPrice:
      allOf:
        - type: object
          properties:
            arrivalDate:
              type: string
              format: date
            variety:
              type: string
""",
        pack_name="MandiPrice",
        shared_yaml=None,
    )

    assert fields["arrivalDate"].format == "date"
    # A plain string carries none, so nothing is claimed about it.
    assert fields["variety"].format is None
