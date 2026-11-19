"""Tier 2 — the fetch writes a tree the loader can read.

The seam test. `pack_file_url` and `examples_api_url` are pinned in tier 1, and
`FilesystemSchemaPackSource` is tested against fixtures — but nothing proves
the two agree on where a file goes. A fetch that writes
`MandiPrice/attributes.yaml` instead of `MandiPrice/v0.1/attributes.yaml`
passes both sides and produces zero packs at boot.

`pytest_httpserver` stands in for GitHub over a real socket, the same way
`tests/integration/adapters/discovery/test_http_client_real_server.py` does
for the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.config.schema_pack_fetch import fetch_packs

_ATTRIBUTES = """
components:
  schemas:
    MandiPrice:
      type: object
      x-jsonld:
        "@context": "https://example.test/MandiPrice/v0.1/context.jsonld"
        "@type": openagrinet:MandiPrice
      allOf:
        - type: object
          required: [informationMode]
          properties:
            informationMode:
              type: string
              enum: [OnDemand, Direct]
"""

_PROFILE = '{"filterable_paths": ["beckn:resourceAttributes.informationMode"]}'
_EXAMPLE = '{"subjectCategories": ["Market"]}'


def _serve_one_pack(httpserver: HTTPServer) -> None:
    base = "/network-specs/schema/MandiPrice/v0.1"
    httpserver.expect_request(f"{base}/attributes.yaml").respond_with_data(_ATTRIBUTES)
    httpserver.expect_request(f"{base}/profile.json").respond_with_data(_PROFILE)
    httpserver.expect_request(f"{base}/examples/onion.json").respond_with_data(_EXAMPLE)

    # The Contents API listing, which is how example filenames are learned —
    # plain HTTP cannot list a directory.
    httpserver.expect_request(
        "/repos/OpenAgriNet/network-specs/contents/schema/MandiPrice/v0.1/examples",
        query_string="ref=main",
    ).respond_with_json([{"name": "onion.json", "type": "file"}])


async def test_the_published_tree_is_one_the_loader_reads(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    _serve_one_pack(httpserver)
    host = httpserver.url_for("").rstrip("/")

    written = fetch_packs(
        dest=tmp_path, packs=("MandiPrice",), pages_host=host, api_host=host
    )

    assert written == ("MandiPrice",)
    packs = await FilesystemSchemaPackSource(root=tmp_path).fetch_packs()
    pack = next(p for p in packs if p.pack_name == "MandiPrice")
    assert pack.version == "v0.1"
    assert pack.examples_json == (_EXAMPLE,)
    assert pack.flattened_fields["informationMode"].enum == ("OnDemand", "Direct")


async def test_the_destination_does_not_have_to_exist(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    _serve_one_pack(httpserver)
    host = httpserver.url_for("").rstrip("/")
    absent = tmp_path / "never" / "created"

    written = fetch_packs(
        dest=absent, packs=("MandiPrice",), pages_host=host, api_host=host
    )

    assert written == ("MandiPrice",)
    assert (absent / "MandiPrice" / "v0.1" / "attributes.yaml").exists()


def test_github_token_goes_only_to_the_examples_listing(
    httpserver: HTTPServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    _serve_one_pack(httpserver)
    host = httpserver.url_for("").rstrip("/")

    fetch_packs(dest=tmp_path, packs=("MandiPrice",), pages_host=host, api_host=host)

    authorization_by_path = {
        request.path: request.headers.get("Authorization")
        for request, _response in httpserver.log
    }
    listing = (
        "/repos/OpenAgriNet/network-specs/contents/schema/MandiPrice/v0.1/examples"
    )
    assert authorization_by_path.pop(listing) == "Bearer secret"
    assert authorization_by_path
    assert set(authorization_by_path.values()) == {None}
