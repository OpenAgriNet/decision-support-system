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
- `cache.py` — `CachedAreaLookup(inner, ttl_seconds, max_entries)`. Bounded
  in-process cache in front of Photon, built on `async-lru`. Caches hits
  and misses, never errors. Simultaneous questions for one name share one
  call to the source, and `cache_info()` gives hit and miss counts.

Extension path: an adopter-supplied source is one more `AreaLookup`
implementation and one more pair in composition. It can be a plain service
that follows the `AreaMatch` contract, or an MCP tool (a follow-up story).
The order is fixed: CSV, then the adopter's source, then Photon. Each is on
only when its URL is set, so an adopter who wants only their own source
leaves `PHOTON_BASE_URL` unset. The chain, core and the port do not change.

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
- **A hand-written cache.** We wrote one first. `async-lru` (8 KB, one
  dependency we already have) does the same and also shares calls for
  simultaneous questions, which ours did not.

## Data model

`AreaMatch` is unchanged. For a Photon hit:
- `name`: Photon `name`.
- `region`: Photon `countrycode` (ISO 3166-1, e.g. `KE`). Coarser than
  the CSV's 3166-2 codes. Finer detail lives in `within`.
- `within`: `(country, state, county)` from the feature properties, empty
  parts dropped, parts equal to `name` dropped, coarsest first. Photon's
  text is kept as is, level words included ("Uasin Gishu County"): the
  question we ask is built from `within`, so the pick matches it either way.
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
`lang=en` because the intent step already returns `place_name` in English,
whatever language the farmer wrote in. A miss from odd romanisation is
accepted: no typo correction, the place is reported as not found.

Settings (all `DSS_` prefixed, in `config/settings.py`):

| Setting | Default | Meaning |
|---|---|---|
| `PHOTON_BASE_URL` | unset | Unset = Photon off |
| `PHOTON_TIMEOUT_SECONDS` | 2.0 | Per call, `anyio.fail_after` |
| `PHOTON_COUNTRY_CODES` | empty | Empty = derive from CSV `region` prefixes |
| `PHOTON_CACHE_ENABLED` | false | Off keeps nothing, which suits providers that forbid storing results |
| `PHOTON_CACHE_TTL_SECONDS` | 86400 | Cache entry life, above 0 |
| `PHOTON_CACHE_MAX_ENTRIES` | 10000 | Most answers kept; the least recently used goes first, above 0 |

Observability:
- Span `dss.area_lookup.photon` with name length, country codes, result
  count, outcome. No place name as an attribute.
- Counter `dss.area_lookup.count` with `source` (`csv`, `photon`) and
  `outcome` (`hit`, `miss`, `error`), recorded by the chain. It counts what
  a source did, not what the farmer is asked: core narrows the matches and
  decides resolved or ambiguous, and the `location` span already says which.
  "photon" counts cached answers too. Cache hit and miss metrics are a
  follow-up. No new stage. One Grafana panel.

## Edge cases

| Case | Expected |
|---|---|
| CSV returns several matches | Chain stops; farmer is asked (#131). Photon not called |
| CSV returns one match | Chain stops; Photon not called |
| CSV misses, Photon unset | Unresolved, as today |
| Photon returns ten features, two named exactly "Rampur" in different counties | Two candidates → ambiguous, same flow as CSV ambiguity |
| Photon returns features, none named exactly as asked | Empty → unresolved. No typo correction |
| Two features with same name and same `within` | One candidate |
| A town inside a county of the same name (Nakuru) | The town stays and the county is dropped. The town's point is exact. Done in the Photon adapter, not core: core keeps the bigger place, which is right for the area file, where a block's point is a copy of its district's |
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
   - [x] Given `AreaLookup.resolve` is `async`, when `resolve_places` runs, then it awaits the lookup and returns the same `Intent` as before
   - [x] Given the CSV adapter, when called, then every existing tier 2 test passes unchanged in meaning
   - [x] Given `FakeAreaLookup` and `test_port_conformance`, when run, then both follow the async shape
2. Chain of sources
   - [x] Given two named fakes, when the first returns a non-empty list, then the second is never called and the counter records the first's name as `source`
   - [x] Given the first returns empty, when the second returns matches, then those are returned and the counter records `miss` for the first
   - [x] Given the first raises `AreaLookupUnavailable`, when resolving, then the second is tried and the counter records `error` for the first
   - [x] Given all sources return empty, when resolving, then the result is empty
3. Photon adapter
   - [x] Given a recorded "Eldoret" response, when resolving, then one `AreaMatch` with `region="KE"`, `within=("Kenya","Uasin Gishu County","Moiben")` and a point
   - [x] Given a recorded "Rampur" response with ten exact-name features, when resolving, then one candidate per distinct `within` (eight)
   - [x] Given a town inside a county of the same name, when resolving, then only the town is returned
   - [x] Given features that do not match the name exactly, when resolving, then empty
   - [x] Given a 500, a timeout, a connection error, or a malformed body, when resolving, then `AreaLookupUnavailable`
   - [x] Given configured country codes, when the request is built, then one `countrycode=` per code and the five `layer=` values
   - [x] Given a call, when traced, then span `dss.area_lookup.photon` carries result count and outcome (`hit`, `miss`, `error`) and no place name
4. Cache wrapper
   - [x] Given a hit, when asked again within TTL, then the inner lookup is not called
   - [x] Given a miss (empty), when asked again, then the inner lookup is not called
   - [x] Given the inner raises, when asked again, then the inner is called again
   - [x] Given TTL passed, when asked again, then the inner is called
   - [x] Given `max_entries` answers kept, when one more is added, then the least recently used is dropped
   - [x] Given simultaneous questions for one name, when the source is slow, then it is called once
5. Settings and wiring
   - [ ] Given `DSS_PHOTON_BASE_URL` unset, when the app builds, then the chain holds only the CSV
   - [ ] Given it set, when the app builds, then the chain is CSV → Photon with a dedicated `httpx.AsyncClient` closed on `aclose`
   - [ ] Given `DSS_PHOTON_CACHE_ENABLED=true`, when the app builds, then Photon is wrapped in the cache with the configured TTL and size; by default it is not wrapped
   - [ ] Given `DSS_PHOTON_COUNTRY_CODES` empty, when the app builds, then codes are the distinct prefixes of the CSV `region` column
   - [ ] Given `DSS_PHOTON_COUNTRY_CODES=KE,UG`, when the app builds, then Photon gets `KE` and `UG`
   - [ ] Given a bad timeout, TTL or size (≤ 0), when the app starts, then it fails at startup
6. Metrics and dashboard
   - [x] Given a chain resolution, when recorded, then `dss.area_lookup.count` increments with `source` and `outcome`
   - [ ] `grafana/dashboards/dss.json` gets one panel: lookups per source and outcome
7. Docs
   - [ ] (skipped by decision) `docs/ADR/0018-ordered-place-sources.md`: context, options (MCP now, sync+thread, trust ranking, Redis), decision, consequences; the extension path (a plain service or an MCP tool behind the same port, fixed order CSV → adopter's source → Photon, each on when its URL is set) as the recorded follow-up
   - [x] `docs/DSS_ARCHITECTURE.md` updated for the chain and Photon
   - [x] `CLAUDE.md` Tech Stack: one line on place sources
   - [x] `docs/RUNNING.md`: Photon settings, the cache-and-licence note, and the product-owner recipe
   - [x] `docker-compose.photon.yml` running Photon with the GraphHopper Africa index. There is no Kenya-only index on the download site. A separate `photon-index` service downloads and the server starts after it. Tested on Podman: the real first download, a restart (healthy in 13 seconds), a live lookup, and the download paths for a missing file, a cut-off archive, a good one, the same URL again and a changed URL
8. Fixtures
   - [x] Record Eldoret, Rampur and a miss from `photon.komoot.io` once; save under `tests/integration/adapters/area_lookup/fixtures/`. The malformed body is hand-written, because a healthy server cannot produce one. The demo server appears nowhere in CI or defaults

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
