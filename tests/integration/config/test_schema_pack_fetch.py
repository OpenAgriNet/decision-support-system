"""Tier 2 — the fetch writes a tree the loader can read.

The seam test. `raw_url` and `examples_api_url` are pinned in tier 1, and
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


def _serve_one_pack(httpserver: HTTPServer, ref: str) -> None:
    """The three files a pack needs, plus the examples listing."""

    base = f"/OpenAgriNet/network-specs/{ref}/schema/MandiPrice/v0.1"
    httpserver.expect_request(f"{base}/attributes.yaml").respond_with_data(_ATTRIBUTES)
    httpserver.expect_request(f"{base}/profile.json").respond_with_data(_PROFILE)
    httpserver.expect_request(f"{base}/examples/onion.json").respond_with_data(_EXAMPLE)

    # The Contents API listing, which is how example filenames are learned —
    # plain HTTP cannot list a directory.
    httpserver.expect_request(
        "/repos/OpenAgriNet/network-specs/contents/schema/MandiPrice/v0.1/examples",
        query_string=f"ref={ref}",
    ).respond_with_json([{"name": "onion.json", "type": "file"}])


async def test_the_fetched_tree_is_one_the_loader_reads(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    """Fetch, then load what was fetched.

    Asserting on the written paths would pin this test to the layout twice
    over. Loading it instead means the two halves have to agree, which is the
    only thing that matters — and the flattened field proves the yaml arrived
    intact, not just that a file of the right name exists.
    """

    _serve_one_pack(httpserver, ref="schema-packs-v0.1")

    written = fetch_packs(
        ref="schema-packs-v0.1",
        dest=tmp_path,
        packs=("MandiPrice",),
        raw_host=httpserver.url_for("").rstrip("/"),
        api_host=httpserver.url_for("").rstrip("/"),
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
    """The fresh-clone case, which is the one the startup fetch is for.

    The whole point of fetching at boot is that the pack directory is not
    there yet, so a fetch that needs it to exist fails exactly when it is
    needed.
    """

    _serve_one_pack(httpserver, ref="schema-packs-v0.1")
    absent = tmp_path / "never" / "created"

    written = fetch_packs(
        ref="schema-packs-v0.1",
        dest=absent,
        packs=("MandiPrice",),
        raw_host=httpserver.url_for("").rstrip("/"),
        api_host=httpserver.url_for("").rstrip("/"),
    )

    assert written == ("MandiPrice",)
    assert (absent / "MandiPrice" / "v0.1" / "attributes.yaml").exists()
