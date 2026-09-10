"""Tier 1 — the URLs and failure rules of the schema-pack fetch.

Pure functions only: what URL a pack file lives at, and what happens when a
ref carries no packs. The fetch itself — sockets, disk, the two halves
agreeing — is tier 2, in `tests/integration/config/`.
"""

from __future__ import annotations

import pytest

from dss.config.schema_pack_fetch import (
    PACKS,
    SchemaPackFetchFailed,
    examples_api_url,
    raise_for_empty,
    raw_url,
)


def test_raw_url_points_at_the_file_on_the_given_ref() -> None:
    """The ref is part of the path, not a query parameter.

    `main` carries only a README today, so the ref is the difference between
    a pack and a 404 — it has to be visible in the URL a failure reports.
    """

    url = raw_url(ref="schema-packs-v0.1", pack="MandiPrice", filename="profile.json")

    assert url == (
        "https://raw.githubusercontent.com/OpenAgriNet/network-specs/"
        "schema-packs-v0.1/schema/MandiPrice/v0.1/profile.json"
    )


def test_the_examples_url_carries_the_ref_as_a_query_parameter() -> None:
    """`examples/` filenames are not knowable up front, so they are listed.

    Plain HTTP cannot list a directory, so this one call per pack asks the
    Contents API instead. Unlike the raw host the ref is a query parameter
    here — and omitting it silently lists the default branch, which carries
    no packs.
    """

    url = examples_api_url(ref="schema-packs-v0.1", pack="MandiPrice")

    assert url == (
        "https://api.github.com/repos/OpenAgriNet/network-specs/contents/"
        "schema/MandiPrice/v0.1/examples?ref=schema-packs-v0.1"
    )


def test_a_ref_with_no_packs_fails_and_names_the_ref() -> None:
    """Zero packs is a failure, not an empty success.

    This is the common case, not an edge one: the default ref carries only a
    README, so a run that passes no `--ref` lands here. The message has to say
    which ref was tried and where the packs actually are, or it reads as a bug
    in the fetch rather than a ref that has nothing on it.
    """

    with pytest.raises(SchemaPackFetchFailed) as caught:
        raise_for_empty(fetched=(), ref="main")

    message = str(caught.value)
    assert "main" in message  # which ref was tried
    assert f"0 of {len(PACKS)}" in message  # how much was found
    assert "schema-packs-v0.1" in message  # where the packs are
