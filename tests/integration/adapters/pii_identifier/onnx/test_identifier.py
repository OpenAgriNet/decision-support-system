"""Tier 2 — the ONNX name identifier.

Most tests use a fake inference session, so they run without the 168 MB model.
The last ones load a real model folder when ``DSS_TEST_ONNX_NER_DIR`` points at
one, and are skipped otherwise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

from tokenizers import Tokenizer, models, normalizers, pre_tokenizers  # noqa: E402

from dss.adapters.pii_identifier.onnx.identifier import OnnxIdentifier  # noqa: E402
from dss.adapters.pii_identifier.onnx.models import OnnxSettings  # noqa: E402
from dss.ports.pii_identifier import IdentifierUnavailable  # noqa: E402

LABELS = {0: "O", 1: "B-PER", 2: "I-PER", 3: "B-LOC", 4: "I-LOC"}


def tiny_tokenizer() -> Tokenizer:
    """A word-level tokenizer with a lower-casing, accent-stripping normaliser,
    like IndicNER's — so offsets must still point into the original text."""

    vocab = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "my": 3, "name": 4, "is": 5,
             "ramesh": 6, "patil": 7, "pune": 8, "से": 9}  # fmt: skip
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.normalizer = normalizers.Sequence(
        [normalizers.NFD(), normalizers.Lowercase(), normalizers.StripAccents()]
    )
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer


class FakeSession:
    """Returns logits that tag each token id with a chosen label."""

    def __init__(self, label_for_id: dict[int, int], inputs: list[str]) -> None:
        self._label_for_id = label_for_id
        self._inputs = inputs
        self.fed: dict[str, np.ndarray] = {}

    def get_inputs(self):
        return [type("I", (), {"name": n})() for n in self._inputs]

    def run(self, _outputs, feed):
        self.fed = feed
        ids = feed["input_ids"][0]
        logits = np.full((1, len(ids), len(LABELS)), -5.0, dtype=np.float32)
        for i, token_id in enumerate(ids):
            logits[0, i, self._label_for_id.get(int(token_id), 0)] = 5.0
        return [logits]


def make(session: FakeSession) -> OnnxIdentifier:
    return OnnxIdentifier(
        session=session,
        tokenizer=tiny_tokenizer(),
        labels=LABELS,
        entity="person",
        min_score=0.8,
    )


async def find(identifier: OnnxIdentifier, text: str):
    [spans] = await identifier.identify([text])
    return spans


async def test_a_name_is_found_at_its_place_in_the_original_text() -> None:
    session = FakeSession({6: 1, 7: 2}, ["input_ids", "attention_mask"])
    text = "My name is Ramesh Patil"
    [candidate] = await find(make(session), text)
    assert text[candidate.start : candidate.end] == "Ramesh Patil"
    assert candidate.value == "Ramesh Patil"
    assert candidate.source == "onnx"
    assert candidate.score > 0.8


async def test_offsets_survive_accent_stripping() -> None:
    # "Rámesh" normalises to "ramesh"; the span must still cover "Rámesh".
    session = FakeSession({6: 1}, ["input_ids", "attention_mask"])
    text = "my name is Rámesh"
    [candidate] = await find(make(session), text)
    assert text[candidate.start : candidate.end] == "Rámesh"


async def test_places_are_not_names() -> None:
    session = FakeSession({8: 3}, ["input_ids", "attention_mask"])
    assert await find(make(session), "pune") == []


async def test_token_type_ids_are_fed_when_the_model_wants_them() -> None:
    session = FakeSession({}, ["input_ids", "attention_mask", "token_type_ids"])
    await find(make(session), "my name")
    assert set(session.fed) == {"input_ids", "attention_mask", "token_type_ids"}
    assert not session.fed["token_type_ids"].any()


async def test_empty_text_finds_nothing() -> None:
    session = FakeSession({}, ["input_ids", "attention_mask"])
    assert await find(make(session), "") == []


def test_a_missing_model_folder_says_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(IdentifierUnavailable, match="model.onnx"):
        OnnxIdentifier.from_settings(OnnxSettings(dir=tmp_path))


def test_a_folder_without_labels_says_so(tmp_path: Path) -> None:
    (tmp_path / "model.onnx").write_bytes(b"")
    tiny_tokenizer().save(str(tmp_path / "tokenizer.json"))
    with pytest.raises(IdentifierUnavailable, match="labels.json"):
        OnnxIdentifier.from_settings(OnnxSettings(dir=tmp_path))


REAL_DIR = os.environ.get("DSS_TEST_ONNX_NER_DIR")
real = pytest.mark.skipif(
    not REAL_DIR, reason="set DSS_TEST_ONNX_NER_DIR to a model folder to run"
)


@pytest.fixture(scope="module")
def indicner() -> OnnxIdentifier:
    assert REAL_DIR is not None
    return OnnxIdentifier.from_settings(OnnxSettings(dir=Path(REAL_DIR), min_score=0.5))


@real
@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("My name is Ramesh Patil and my cow has fever", "Ramesh Patil"),
        ("मेरा नाम रमेश पाटिल है, मेरी गाय को बुखार है।", "रमेश पाटिल"),
        ("माझे नाव संजय जाधव आहे", "संजय जाधव"),
    ],
)
async def test_a_real_model_finds_the_name(
    indicner: OnnxIdentifier, text: str, name: str
) -> None:
    found = [text[c.start : c.end] for c in await find(indicner, text)]
    assert name in found


@real
def test_a_real_model_folder_has_its_labels() -> None:
    assert REAL_DIR is not None
    labels = json.loads((Path(REAL_DIR) / "labels.json").read_text())
    assert "B-PER" in labels.values()
