"""Tier 1 — the fetch script's exit code and arguments.

`fetch_packs` is tested elsewhere. What only the script can get wrong is the
shell contract: a failed fetch must exit non-zero, or CI and a container's
entrypoint will carry on as though packs were downloaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.schema_pack_fetch import SchemaPackFetchFailedError
from scripts.fetch_schema_packs import main


def test_a_failed_fetch_exits_non_zero(monkeypatch, tmp_path: Path) -> None:
    def refuses(**_kwargs):
        raise SchemaPackFetchFailedError("no published schema packs found (0 of 6).")

    monkeypatch.setattr("scripts.fetch_schema_packs.fetch_packs", refuses)

    with pytest.raises(SystemExit) as caught:
        main(["--dest", str(tmp_path)])

    assert caught.value.code == 1


def test_the_destination_is_passed_to_the_fetch(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def record(**kwargs):
        seen.update(kwargs)
        return ("MandiPrice",)

    monkeypatch.setattr("scripts.fetch_schema_packs.fetch_packs", record)

    main(["--dest", str(tmp_path)])

    assert seen == {"dest": tmp_path}
