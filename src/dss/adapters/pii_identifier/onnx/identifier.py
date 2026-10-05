"""People's names by any token-classification model exported to ONNX — IndicNER
today — behind the ``PiiIdentifier`` port.

The model folder holds ``model.onnx``, ``tokenizer.json`` and ``labels.json``
(label id → tag such as ``B-PER``). ``scripts/export_onnx_ner.py`` builds it.
Another model is a different folder, not a code change.

Needs ``onnxruntime`` and ``tokenizers`` (``uv sync --extra ner-indic``); no
torch. The tokenizer may lower-case and strip accents, but its offsets point
into the original text, so a span is cut from what the farmer wrote.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anyio.to_thread
import numpy as np

from dss.adapters.pii_identifier.onnx.decode import Token, name_spans
from dss.adapters.pii_identifier.onnx.models import OnnxSettings
from dss.core.redaction.models import PiiSpan
from dss.ports.pii_identifier import IdentifierUnavailable

_FILES = ("model.onnx", "tokenizer.json", "labels.json")
# BERT-style models take at most 512 tokens; a farmer's question is far shorter.
_MAX_TOKENS = 512


class OnnxIdentifier:
    name = "onnx"

    def __init__(
        self,
        *,
        session: Any,
        tokenizer: Any,
        labels: dict[int, str],
        entity: str,
        min_score: float,
    ) -> None:
        self._session = session
        self._inputs = {i.name for i in session.get_inputs()}
        self._tokenizer = tokenizer
        self._labels = labels
        self._entity = entity
        self._min_score = min_score

    @classmethod
    def from_settings(cls, settings: OnnxSettings) -> OnnxIdentifier:
        """Load the model folder. Anything missing raises
        ``IdentifierUnavailable`` naming it, so the boot stops with a reason."""

        folder = settings.dir
        for name in _FILES:
            if not (folder / name).is_file():
                raise IdentifierUnavailable(
                    f"name model folder {folder} has no {name} — build it with "
                    "scripts/export_onnx_ner.py"
                )
        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise IdentifierUnavailable(
                "the onnx identifier needs onnxruntime and tokenizers — "
                "run `uv sync --extra ner-indic`"
            ) from exc

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        session = onnxruntime.InferenceSession(
            str(folder / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        tokenizer.enable_truncation(_MAX_TOKENS)
        raw = json.loads((folder / "labels.json").read_text(encoding="utf-8"))
        return cls(
            session=session,
            tokenizer=tokenizer,
            labels={int(k): v for k, v in raw.items()},
            entity=settings.entity,
            min_score=settings.min_score,
        )

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        # CPU-bound: keep it off the event loop.
        return await anyio.to_thread.run_sync(
            lambda: [self._find(text) for text in texts]
        )

    def _find(self, text: str) -> list[PiiSpan]:
        if not text.strip():
            return []

        encoding = self._tokenizer.encode(text)
        ids = np.array([encoding.ids], dtype=np.int64)
        feed = {
            "input_ids": ids,
            "attention_mask": np.array([encoding.attention_mask], dtype=np.int64),
            "token_type_ids": np.zeros_like(ids),
        }
        feed = {name: value for name, value in feed.items() if name in self._inputs}
        logits = self._session.run(None, feed)[0][0]

        shifted = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probabilities = shifted / shifted.sum(axis=-1, keepdims=True)
        best = probabilities.argmax(axis=-1)

        tokens = [
            Token(
                label=self._labels[int(best[i])],
                score=float(probabilities[i, best[i]]),
                start=start,
                end=end,
                word=word,
            )
            for i, ((start, end), word) in enumerate(
                zip(encoding.offsets, encoding.word_ids, strict=True)
            )
        ]
        return [
            PiiSpan(
                start=start,
                end=end,
                entity=self._entity,
                score=score,
                source=self.name,
                value=text[start:end],
            )
            for start, end, score in name_spans(tokens, min_score=self._min_score)
        ]
