"""The regex identifier's rules and the entity policy the tests share — the same
set the example file ships. Built in Python so tests read no files.
"""

from __future__ import annotations

from dss.adapters.pii_identifier.regex.models import (
    DeclaringPhraseRule,
    PatternRule,
    RegexSettings,
)
from dss.core.redaction.models import RedactionPolicy, ValueHandling

KEEP = ValueHandling.KEEP
DESTROY = ValueHandling.DESTROY

AADHAAR = PatternRule(
    entity="aadhaar",
    pattern=r"(?<!\d)[2-9]\d{11}(?!\d)",
    validator="verhoeff",
    groupings=[[4, 4, 4]],
)
CARD = PatternRule(
    entity="card",
    pattern=r"(?<!\d)[1-9]\d{12,18}(?!\d)",
    validator="luhn",
    groupings=[[4, 4, 4, 4], [4, 6, 5]],
)
GSTIN = PatternRule(
    entity="gstin",
    pattern=r"(?i)\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b",
    validator="gstin",
)
PAN = PatternRule(entity="pan", pattern=r"(?i)\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b")
IFSC = PatternRule(entity="ifsc", pattern=r"(?i)\b[A-Z]{4}0[A-Z0-9]{6}\b")
PHONE = PatternRule(
    entity="phone",
    pattern=r"(?<![\d+])(?:(?:\+|00)?91|0)?[6-9]\d{9}(?!\d)",
)
PM_KISAN_ID = PatternRule(entity="pm_kisan_id", pattern=r"(?i)\b[A-Z]{2}\d{9}\b")
FARMER_ID = PatternRule(
    entity="farmer_id",
    pattern=r"(?<![\dA-Za-z])\d{11}(?![\dA-Za-z])",
    groupings=[[11]],
)
FARM_ID = PatternRule(entity="farm_id", pattern=r"(?i)\b[A-Z]{2}\d{12}\b")
EMAIL = PatternRule(
    entity="email",
    pattern=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
)
PERSON = DeclaringPhraseRule(
    entity="person",
    phrases=["my name is", "my name's"],
    stopwords=["and", "from", "in", "i", "my", "here", "please", "sir", "madam"],
)

SETTINGS = RegexSettings(
    rules=[
        AADHAAR,
        CARD,
        GSTIN,
        PAN,
        IFSC,
        PHONE,
        PM_KISAN_ID,
        FARMER_ID,
        FARM_ID,
        EMAIL,
        PERSON,
    ],
)

POLICY = RedactionPolicy(
    entities={
        "aadhaar": DESTROY,
        "card": DESTROY,
        "gstin": KEEP,
        "pan": KEEP,
        "ifsc": KEEP,
        "phone": KEEP,
        "pm_kisan_id": KEEP,
        "farmer_id": KEEP,
        "farm_id": KEEP,
        "email": KEEP,
        "person": KEEP,
    }
)
