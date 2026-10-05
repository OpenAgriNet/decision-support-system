"""Export a Hugging Face name-finding model to a small ONNX folder (ADR-0016).

Writes, into ``--out``:

- ``model.onnx``      the model, compressed to int8 (IndicNER: 667 MB → 168 MB)
- ``tokenizer.json``  the tokenizer
- ``labels.json``     label id → tag (``B-PER``, ``I-PER``, ...)
- ``manifest.json``   which model it came from, and a SHA-256 per file

The DSS loads that folder with onnxruntime alone. This script needs torch and
transformers, which the DSS never installs; pull them in for this run only:

    HF_TOKEN=... uv run --no-project --python 3.13 \\
        --with torch --with transformers --with onnx --with onnxruntime \\
        python scripts/export_onnx_ner.py --out var/models/indicner

``ai4bharat/IndicNER`` is gated (accept the terms on Hugging Face once; approval
is automatic), hence ``HF_TOKEN``. ``--model`` takes any token-classification
model with a fast tokenizer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

DEFAULT_MODEL = "ai4bharat/IndicNER"
_INPUTS = ("input_ids", "attention_mask", "token_type_ids")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if not tokenizer.is_fast:
        print(f"{args.model} has no fast tokenizer; cannot write tokenizer.json")
        return 1
    model = AutoModelForTokenClassification.from_pretrained(args.model).eval()

    sample = tokenizer("my name is Ramesh Patil", return_tensors="pt")
    names = [name for name in _INPUTS if name in sample]
    axes = {name: {0: "batch", 1: "tokens"} for name in [*names, "logits"]}

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        full = Path(scratch) / "model.onnx"
        torch.onnx.export(
            model,
            tuple(sample[name] for name in names),
            str(full),
            input_names=names,
            output_names=["logits"],
            dynamic_axes=axes,
            opset_version=17,
            dynamo=False,
        )
        quantize_dynamic(
            str(full), str(args.out / "model.onnx"), weight_type=QuantType.QInt8
        )

    tokenizer.backend_tokenizer.save(str(args.out / "tokenizer.json"))
    labels = {str(k): v for k, v in model.config.id2label.items()}
    _write_json(args.out / "labels.json", labels)

    files = ("model.onnx", "tokenizer.json", "labels.json")
    manifest = {
        "source": args.model,
        "revision": getattr(model.config, "_commit_hash", None),
        "sha256": {f: _hash_file(args.out / f) for f in files},
    }
    _write_json(args.out / "manifest.json", manifest)

    size = (args.out / "model.onnx").stat().st_size / 1e6
    print(f"wrote {args.out}: model.onnx {size:.1f} MB")
    return 0


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
