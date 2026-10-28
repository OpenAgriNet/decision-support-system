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
    """The script's whole reason to exist.

    A fetch that finds nothing raises, and the message already says which ref
    was tried. The script's job is to turn that into an exit code the shell
    can act on, and to print the reason rather than a traceback.
    """

    def refuses(**_kwargs):
        raise SchemaPackFetchFailedError(
            "no schema packs found on ref 'main' (0 of 4)."
        )

    monkeypatch.setattr("scripts.fetch_schema_packs.fetch_packs", refuses)

    with pytest.raises(SystemExit) as caught:
        main(["--dest", str(tmp_path)])

    assert caught.value.code == 1


def test_the_ref_is_an_argument_and_defaults_to_the_published_ref(
    monkeypatch, tmp_path
) -> None:
    seen: dict = {}

    def record(**kwargs):
        seen.update(kwargs)
        return ("MandiPrice",)

    monkeypatch.setattr("scripts.fetch_schema_packs.fetch_packs", record)

    main(["--dest", str(tmp_path)])
    assert seen["ref"] == "schema-packs-v0.1"

    main(["--ref", "main", "--dest", str(tmp_path)])
    assert seen["ref"] == "main"
