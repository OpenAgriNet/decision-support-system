"""Tier 1 — the URLs and failure rules of the schema-pack fetch.

Pure functions only: what URL a pack file lives at, and what happens when a
fetch finds no packs. The fetch itself — sockets, disk, the two halves
agreeing — is tier 2, in `tests/integration/config/`.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dss.config import schema_pack_fetch
from dss.config.schema_pack_fetch import (
    PACKS,
    SchemaPackFetchFailedError,
    examples_api_url,
    fetch_packs,
    pack_file_url,
    raise_for_empty,
)


def test_a_pack_file_is_read_from_the_published_site() -> None:
    url = pack_file_url(pack="MandiPrice", filename="profile.json")

    assert url == (
        "https://openagrinet.github.io/network-specs/"
        "schema/MandiPrice/v0.1/profile.json"
    )


def test_the_examples_are_listed_from_main() -> None:
    url = examples_api_url(pack="MandiPrice")

    assert url == (
        "https://api.github.com/repos/OpenAgriNet/network-specs/contents/"
        "schema/MandiPrice/v0.1/examples?ref=main"
    )


def test_github_token_goes_only_to_the_api_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    authorization_by_url: dict[str, str | None] = {}

    def answers_everything(request, timeout):
        authorization_by_url[request.full_url] = request.get_header("Authorization")
        return io.BytesIO(b"[]")

    monkeypatch.setattr(schema_pack_fetch.urllib.request, "urlopen", answers_everything)

    fetch_packs(dest=tmp_path, packs=("MandiPrice",))

    pages = _authorizations_for(authorization_by_url, "https://openagrinet.github.io/")
    api = _authorizations_for(authorization_by_url, "https://api.github.com/")
    assert pages
    assert set(pages) == {None}
    assert api == ["Bearer secret"]


def _authorizations_for(
    authorization_by_url: dict[str, str | None], prefix: str
) -> list[str | None]:
    return [
        header for url, header in authorization_by_url.items() if url.startswith(prefix)
    ]


def test_nothing_published_says_what_to_check() -> None:
    with pytest.raises(SchemaPackFetchFailedError) as caught:
        raise_for_empty(fetched=())

    message = str(caught.value)
    assert f"0 of {len(PACKS)}" in message
    assert "openagrinet.github.io" in message
    assert "GITHUB_TOKEN" in message
