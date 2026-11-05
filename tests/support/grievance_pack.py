from __future__ import annotations

from pathlib import Path

from dss.core.forms.models import ActionSchema, FormContext
from dss.core.forms.registry import FormRegistry
from dss.core.forms.service import build_form_registry, parse_api_pack
from dss.core.schema_packs.models import SchemaPackFiles

# The Grievance pack, untrimmed, as network-specs first published it. It lives on
# `schema-packs-v0.1` now; its URLs here still say `api-schemas/`.
GRIEVANCE_YAML = (
    Path(__file__).parents[1] / "fixtures" / "api_packs" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

API_PROFILE = '{"semantic_model": "provider-api"}'

PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")
PMKISAN_INIT = FormContext(scheme="PM-KISAN", category="Grievance", action="init")


def build_grievance_pack(
    attributes_yaml: str = GRIEVANCE_YAML,
    *,
    pack_name: str = "Grievance",
    profile_json: str = API_PROFILE,
) -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name=pack_name,
        version="v0.1",
        profile_json=profile_json,
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )


def find_action_schema(
    context: FormContext, attributes_yaml: str = GRIEVANCE_YAML
) -> ActionSchema:
    schemas = parse_api_pack(build_grievance_pack(attributes_yaml))
    return next(schema for schema in schemas if schema.context == context)


def build_grievance_forms() -> FormRegistry:
    return build_form_registry([build_grievance_pack()])
