"""Rules the redaction tests share — the same set the example file ships.

Built in Python so tier-1 tests read no files. The loader tests check that the
shipped example parses to rules that behave like these.
"""

from __future__ import annotations

from dss.core.redaction.models import (
    DeclaringPhraseRule,
    PatternRule,
    RedactionConfig,
    ValueHandling,
)

KEEP = ValueHandling.KEEP
DESTROY = ValueHandling.DESTROY

AADHAAR = PatternRule(
    entity="aadhaar",
    pattern=r"(?<!\d)[2-9]\d{11}(?!\d)",
    validator="verhoeff",
    value=DESTROY,
)
CARD = PatternRule(
    entity="card", pattern=r"(?<!\d)\d{13,19}(?!\d)", validator="luhn", value=DESTROY
)
GSTIN = PatternRule(
    entity="gstin",
    pattern=r"(?i)\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b",
    validator="gstin",
    value=KEEP,
)
PAN = PatternRule(
    entity="pan", pattern=r"(?i)\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b", value=KEEP
)
IFSC = PatternRule(entity="ifsc", pattern=r"(?i)\b[A-Z]{4}0[A-Z0-9]{6}\b", value=KEEP)
PHONE = PatternRule(
    entity="phone",
    pattern=r"(?<![\d+])(?:(?:\+|00)?91|0)?[6-9]\d{9}(?!\d)",
    value=KEEP,
)
EMAIL = PatternRule(
    entity="email",
    pattern=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    value=KEEP,
)
PERSON = DeclaringPhraseRule(
    entity="person",
    phrases=["my name is", "my name's"],
    stopwords=["and", "from", "in", "i", "my", "here", "please", "sir", "madam"],
    value=KEEP,
)

CONFIG = RedactionConfig(
    rules=[AADHAAR, CARD, GSTIN, PAN, IFSC, PHONE, EMAIL, PERSON],
)
