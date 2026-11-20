"""Tier 1 — the form-labels loader and the shipped default labels."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.form_labels_loader import load_form_labels
from dss.core.forms.models import FormLabels
from dss.core.forms.service import build_form_registry
from tests.support.grievance_pack import build_grievance_pack


def test_the_shipped_labels_ask_pm_kisan_farmers_to_enter_the_otp() -> None:
    labels = load_form_labels()

    assert labels.label_for("PM-KISAN", "challenge.response.value") == "Enter OTP"


def test_every_shipped_label_matches_a_field_of_the_grievance_pack() -> None:
    registry = build_form_registry([build_grievance_pack()], labels=load_form_labels())

    assert registry.unused_labels == ()


def test_labels_load_from_a_given_file(tmp_path: Path) -> None:
    path = tmp_path / "form-labels.yaml"
    path.write_text("PMFBY:\n  cropYear: Season year\n", encoding="utf-8")

    assert load_form_labels(path) == FormLabels(
        by_scheme={"PMFBY": {"cropYear": "Season year"}}
    )


def test_a_missing_labels_file_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_form_labels(tmp_path / "does-not-exist.yaml")
