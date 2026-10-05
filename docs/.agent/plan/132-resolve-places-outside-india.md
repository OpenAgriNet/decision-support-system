# Spec: Resolve places outside India

Story: OpenAgriNet/engineering-tracker#132
PRD: none
Branch: `feat/132-resolve-places-outside-india` (on top of #131)

## Architecture / approach

Place lookup becomes an ordered chain behind the existing `AreaLookup`
port: the CSV first, then Photon over HTTP as the fallback for names the
CSV does not carry. In India that is villages, since `areas.csv` stops at
blocks. Elsewhere it is everything the adopter's own file misses. The
chain stops at the first source that returns a non-empty list. Photon is
on only when its URL is set. Core sees one `AreaLookup`, as today.

Pieces, all under `src/dss/adapters/area_lookup/`:
- `chain.py` — `ChainedAreaLookup(sources)` where `sources` is a list of
  `(name, lookup)` pairs, e.g. `[("csv", csv), ("photon", cached_photon)]`.
  Tries each in order. A source that raises `AreaLookupUnavailable` counts
  as empty and the next is tried. Records the lookup counter with the
  pair's name as `source`.
- `photon.py` — `PhotonAreaLookup`. One GET to `/api`. Maps features to
  `AreaMatch`. Raises `AreaLookupUnavailable` on any failure.
- `cache.py` — `CachedAreaLookup(inner, ttl)`. Bounded in-process cache in
  front of Photon. Caches hits and misses, never errors.

Extension path: a future adopter-supplied source (the MCP story) is one
more `AreaLookup` implementation and one more pair in composition. The
chain, core and the port do not change.

The port and `resolve_places` become `async`. The CSV adapter becomes
`async def` with no await inside.

A non-India adopter replaces `areas.csv` with their own file via
`DSS_AREA_CSV_PATH` and points Photon at their instance. Order is fixed;
no order setting.

Alternatives rejected:
- **MCP server for Photon now.** Adds the `mcp` dependency, a second
  deployable, a tool schema to version, and a transport choice, before any
  adopter needs a custom source. The port is the tool contract. An
  `McpAreaLookup` for adopter-supplied sources is a follow-up story; the
  ADR records it.
- **Sync port with a worker thread.** Hides a network call behind a sync
  face; timeouts get awkward. Async is the honest shape.
- **Trust Photon's top result.** The story's first risk is a confident
  wrong match. We filter and ask instead.
- **Redis cache.** New port, adapter, infra. Nothing else uses Redis yet.
  The wrapper sits behind the port, so Redis can replace it later.

## Data model

`AreaMatch` is unchanged. For a Photon hit:
- `name`: Photon `name`.
- `region`: Photon `countrycode` (ISO 3166-1, e.g. `KE`). Coarser than
  the CSV's 3166-2 codes. Finer detail lives in `within`.
- `within`: `(country, state, county)` from the feature properties, empty
  parts dropped, parts equal to `name` dropped, coarsest first.
- `geometry`: the feature's point, longitude first.
Nothing Photon-specific (`osm_id`, `osm_key`) enters the model.

New exception in `ports/area_lookup.py`: `AreaLookupUnavailable`. Core
never sees it; the chain catches it.

## API contracts

`AreaLookup.resolve` becomes `async def resolve(self, name, region=None)
-> list[AreaMatch]`. Same meaning: empty, one, several. `resolve_places`
becomes `async`; `run_turn` awaits it. No HTTP contract change on
`/v1/turns`.

Photon request: `GET {base_url}/api?q=<name>&limit=10&lang=en`
`&layer=city&layer=district&layer=locality&layer=county&layer=state`
plus one `countrycode=` per configured code. No `lat`/`lon` bias (#131:
do not guess from where the farmer is). The port's `region` argument is
ignored by this adapter; the country filter comes from settings.
<!-- inferred — verify: `lang=en` vs server default (local name) -->

Settings (all `DSS_` prefixed, in `config/settings.py`):

| Setting | Default | Meaning |
|---|---|---|
| `PHOTON_BASE_URL` | unset | Unset = Photon off |
| `PHOTON_TIMEOUT_SECONDS` | 2.0 | Per call, `anyio.fail_after` |
| `PHOTON_COUNTRY_CODES` | empty | Empty = derive from CSV `region` prefixes |
| `PHOTON_CACHE_ENABLED` | true | Off for providers that forbid storing results |
| `PHOTON_CACHE_TTL_SECONDS` | 86400 | Cache entry life |

Cache size is a constant (10,000 entries).

Observability:
- Span `dss.area_lookup.photon` with name length, country codes, result
  count, outcome. No place name as an attribute.
- Counter `dss.place.lookup.count` with `source` (`csv`, `photon`,
  `cache`) and `outcome` (`resolved`, `ambiguous`, `miss`, `error`),
  recorded by the chain. No new stage. One Grafana panel.

## Edge cases

| Case | Expected |
|---|---|
| CSV returns several matches | Chain stops; farmer is asked (#131). Photon not called |
| CSV returns one match | Chain stops; Photon not called |
| CSV misses, Photon unset | Unresolved, as today |
| Photon returns ten features, two named exactly "Rampur" in different counties | Two candidates → ambiguous, same flow as CSV ambiguity |
| Photon returns features, none named exactly as asked | Empty → unresolved. No typo correction |
| Two features with same name and same `within` | One candidate |
| Feature without a point geometry | Skipped |
| State-layer feature where `state` equals `name` | `within` = `(country,)` |
| Name with a comma ("Rampur, Himachal") | Core splits first; Photon gets "Rampur"; the part is matched against `within` as today |
| Device geometry present | Lookup not called at all (unchanged) |
| `PHOTON_COUNTRY_CODES` set and CSV has other prefixes | Setting wins |
| Same name asked twice within TTL | Second call served from cache, counted as `source=cache` |
| Photon call fails | Not cached; next call tries again |

## Failure modes

| Failure | Expected |
|---|---|
| Photon slower than timeout | `AreaLookupUnavailable` → chain counts `error`, result is unresolved. Turn completes |
| Connection refused / DNS | Same |
| Non-2xx | Same |
| Malformed JSON body | Same |
| `PHOTON_BASE_URL` set but unreachable at startup | App boots. Failures surface per turn, not at boot |
| `PHOTON_BASE_URL` unset | Chain is CSV alone. Zero behaviour change for existing deployments |

No retries. A place lookup is a hint, not the answer.

## Implementation tasks

1. Make the port async
   - [ ] Given `AreaLookup.resolve` is `async`, when `resolve_places` runs, then it awaits the lookup and returns the same `Intent` as before
   - [ ] Given the CSV adapter, when called, then every existing tier 2 test passes unchanged in meaning
   - [ ] Given `FakeAreaLookup` and `test_port_conformance`, when run, then both follow the async shape
2. Chain of sources
   - [ ] Given two named fakes, when the first returns a non-empty list, then the second is never called and the counter records the first's name as `source`
   - [ ] Given the first returns empty, when the second returns matches, then those are returned
   - [ ] Given the first raises `AreaLookupUnavailable`, when resolving, then the second is tried and the counter records `error` for the first
   - [ ] Given all sources return empty, when resolving, then the result is empty
3. Photon adapter
   - [ ] Given a recorded "Eldoret" response, when resolving, then one `AreaMatch` with `region="KE"`, `within=("Kenya","Uasin Gishu")` and a point
   - [ ] Given a recorded "Rampur" response with two exact-name features, when resolving, then two candidates
   - [ ] Given features that do not match the name exactly, when resolving, then empty
   - [ ] Given a 500, a timeout, a connection error, or a malformed body, when resolving, then `AreaLookupUnavailable`
   - [ ] Given configured country codes, when the request is built, then one `countrycode=` per code and the five `layer=` values
   - [ ] Given a call, when traced, then span `dss.area_lookup.photon` carries result count and outcome and no place name
4. Cache wrapper
   - [ ] Given a hit, when asked again within TTL, then the inner lookup is not called
   - [ ] Given a miss (empty), when asked again, then the inner lookup is not called
   - [ ] Given the inner raises, when asked again, then the inner is called again
   - [ ] Given TTL passed, when asked again, then the inner is called
   - [ ] Given 10,000 entries, when one more is added, then the oldest is dropped
5. Settings and wiring
   - [ ] Given `DSS_PHOTON_BASE_URL` unset, when the app builds, then the chain holds only the CSV
   - [ ] Given it set, when the app builds, then the chain is CSV → cached Photon with a dedicated `httpx.AsyncClient` closed on `aclose`
   - [ ] Given `DSS_PHOTON_CACHE_ENABLED=false`, when the app builds, then Photon is not wrapped
   - [ ] Given `DSS_PHOTON_COUNTRY_CODES` empty, when the app builds, then codes are the distinct prefixes of the CSV `region` column
   - [ ] Given a bad timeout (≤ 0), when the app starts, then it fails at startup
6. Metrics and dashboard
   - [ ] Given a chain resolution, when recorded, then `dss.place.lookup.count` increments with `source` and `outcome`
   - [ ] `grafana/dashboards/dss.json` gets one panel: lookups per source and outcome
7. Docs
   - [ ] `docs/ADR/0018-ordered-place-sources.md`: context, options (MCP now, sync+thread, trust ranking, Redis), decision, consequences; MCP-backed source as the recorded follow-up
   - [ ] `docs/DSS_ARCHITECTURE.md` updated for the chain and Photon
   - [ ] `CLAUDE.md` Tech Stack: one line on place sources
   - [ ] `docs/RUNNING.md`: Photon settings, the cache-and-licence note, and the product-owner recipe
   - [ ] `docker-compose.photon.yml` running Photon with the GraphHopper Kenya extract
8. Fixtures
   - [ ] Record Eldoret, Rampur, miss, and a malformed body from `photon.komoot.io` once; save under `tests/integration/adapters/area_lookup/fixtures/`. The demo server appears nowhere in CI or defaults

## Test strategy

| Tier | Where | Covers |
|---|---|---|
| 1 | `tests/unit/core/location/` | `resolve_places` async; behaviour unchanged |
| 2 | `tests/integration/adapters/area_lookup/test_chain.py` | Chain with `FakeAreaLookup` |
| 2 | `.../test_photon.py` | `httpx.MockTransport` + recorded fixtures |
| 2 | `.../test_cache.py` | TTL, size, error passthrough |
| 2 | `tests/integration/adapters/observability/` | Counter and span attributes |
| 3 | `tests/integration/orchestration/test_turn.py` | `run_turn` awaits the async lookup |
| manual | RUNNING.md recipe | Real Photon with Kenya extract, "Eldoret" → markets near Eldoret |

No live network in CI. No eval tier change (no LLM in place resolution).
