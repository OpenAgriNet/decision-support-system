"""Tier 1 — the URLs and failure rules of the schema-pack fetch.

Pure functions only: what URL a pack file lives at, and what happens when a
fetch finds no packs. The fetch itself — sockets, disk, the two halves
agreeing — is tier 2, in `tests/integration/config/`.
"""

from __future__ import annotations

import urllib.error
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


def test_with_no_ref_a_pack_file_is_read_from_the_published_site() -> None:
    url = pack_file_url(ref=None, pack="MandiPrice", filename="profile.json")

    assert url == (
        "https://openagrinet.github.io/network-specs/"
        "schema/MandiPrice/v0.1/profile.json"
    )


def test_with_a_ref_a_pack_file_is_read_from_that_branch_preview() -> None:
    url = pack_file_url(ref="my-branch", pack="MandiPrice", filename="profile.json")

    assert url == (
        "https://openagrinet.github.io/network-specs/previews/"
        "my-branch/schema/MandiPrice/v0.1/profile.json"
    )


def test_the_ref_is_encoded_in_the_preview_path() -> None:
    url = pack_file_url(ref="fix/a#b&c", pack="MandiPrice", filename="profile.json")

    assert "/previews/fix/a%23b%26c/schema/" in url


def test_the_examples_url_carries_the_ref_as_a_query_parameter() -> None:
    url = examples_api_url(ref="my-branch", pack="MandiPrice")

    assert url == (
        "https://api.github.com/repos/OpenAgriNet/network-specs/contents/"
        "schema/MandiPrice/v0.1/examples?ref=my-branch"
    )


def test_the_ref_is_encoded_in_the_examples_query() -> None:
    url = examples_api_url(ref="fix/a#b&c", pack="MandiPrice")

    assert url.endswith("examples?ref=fix%2Fa%23b%26c")


def test_with_no_ref_the_examples_are_listed_from_main() -> None:
    url = examples_api_url(ref=None, pack="MandiPrice")

    assert url.endswith("examples?ref=main")


def test_github_token_goes_only_to_the_api_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    sent: dict[str, str | None] = {}

    def not_found(request, timeout):
        sent[request.full_url] = request.get_header("Authorization")
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(schema_pack_fetch.urllib.request, "urlopen", not_found)

    with pytest.raises(SchemaPackFetchFailedError):
        fetch_packs(ref="x/repos/y", dest=tmp_path, packs=("MandiPrice",))

    pages_headers = [
        header
        for url, header in sent.items()
        if url.startswith("https://openagrinet.github.io/")
    ]
    assert pages_headers
    assert all(header is None for header in pages_headers)


def test_a_ref_with_no_preview_fails_and_points_at_the_previews() -> None:
    with pytest.raises(SchemaPackFetchFailedError) as caught:
        raise_for_empty(fetched=(), ref="my-branch")

    message = str(caught.value)
    assert "'my-branch'" in message
    assert f"0 of {len(PACKS)}" in message
    assert "https://openagrinet.github.io/network-specs/previews/" in message


def test_the_no_preview_error_says_main_is_the_published_site() -> None:
    with pytest.raises(SchemaPackFetchFailedError) as caught:
        raise_for_empty(fetched=(), ref="main")

    assert "leave the ref unset" in str(caught.value)


def test_nothing_published_says_what_to_check() -> None:
    with pytest.raises(SchemaPackFetchFailedError) as caught:
        raise_for_empty(fetched=(), ref=None)

    message = str(caught.value)
    assert f"0 of {len(PACKS)}" in message
    assert "openagrinet.github.io" in message
    assert "GITHUB_TOKEN" in message
