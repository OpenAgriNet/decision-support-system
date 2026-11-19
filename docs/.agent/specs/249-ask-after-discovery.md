# Spec: Refactoring: one object per ask after discovery

Story: OpenAgriNet/engineering-tracker#249
Stacked on: `refactor/249-ask-id` (PR 174)

## The model

A turn is about its asks. After "understand", each step takes asks and
gives back the ask with what that step learned:

```
Ask → AskSettlement(ask, settlement) → DiscoveredAsk(ask, query, found, priority) → AskEvidence(ask, outcome)
```

- Each fact carries its ask, so no step keeps one list beside another.
- Each fact holds the ask, not the fact before it. Steps stay independent,
  and the merge by ask id joins them where needed.
- An `Intent` is what the user wants as a whole. It is built once, where
  the asks are made, and no later step takes one.

Discovery already builds a per-ask object inside, then takes it apart into
four dicts keyed by ask id. This story keeps the object and puts the ask
in it.

## Types

```
DiscoveredAsk                    core/provider_discovery
  ask        Ask
  query      ProviderQuery | None     None: never searched
  found      answers, capabilities, failures
  priority   AskPriority | None       None: no ranking applied
  is_served · find_capability(resource_id)

DiscoveryResult
  asks       tuple[DiscoveredAsk, ...]    one per ask given, in that order
  events
  find(ask_id) · serves_any_ask
```

Gone: the four dicts and `serves(ask_id)`, `DiscoveredAsks` and its
`unsearchable_asks` set, `TurnResult.settled_intent`, `_settled_only`,
`Intent.find_ask`, `PlannerDeps.intent`.

## Contracts

```
resolve_places(classification, turn)   -> Intent
settle_asks(asks)                      -> AskSettlement per ask      takes asks
discover(settled asks, turn)           -> DiscoveryResult            takes asks
find_forms(discovery, forms)           -> {ask id: form}             no Intent
plan(turn, discovery, awaiting)        -> Evidence                   no Intent
merge_unsettled(settlements, evidence) -> Evidence
compose(evidence, turn)                -> the reply
```

A form submission: `ask_for_form(...) -> Ask`, then `discover((ask,), turn)`.

No change to `POST /v1/turns`, to any port, or to a tool description.

## Design choices

- **Keep the query on the object.** Asks that search the same thing share
  one query. With the query kept, "which asks share a search" can still be
  asked, which the open fan-out item in `TODO.md` needs.
- **Found per query, ranked per ask.** Two asks can share what was found
  and still be ranked differently, by their own category. So ranking gives
  back a new object per ask and sets its `priority`.
- **The id is resolved once.** A model can only pass a number. The tool
  turns it into the `DiscoveredAsk` at its first line, and the rest reads
  the object. A wrong id is caught at that one line.
- **The planner's running log stays keyed by id.** It is written in call
  order, and source numbers come from that order.
- **Turn-level facts stay at turn level:** confidence, the moderation
  decision, discovery events, the source list.
- **`DiscoveryResult` rejects two entries with one ask id.** Discovery now
  takes a plain list of asks, so the rule needs a home here.

Rejected:
- **Keep the dicts, add a per-ask view.** The dicts can still disagree.
- **One type per outcome** (served, nobody, not searched, failed). No
  reader needs it today.
- **An `Asks` collection type.** Its lookup has one caller.

One visible change: a tool call naming an unknown ask id gets "ask N is
not one of this turn's asks". Today it gets the "not one of ask N's
candidates" text.

## Not in this story

- The form step's dict of ask id to form. Later it can give back
  `AskEvidence(ask, AwaitingInput(form))`, which needs no new type.
- `TurnResult` still holds `intent` beside the settlements.
- One event per query when asks share a search.

## Implementation tasks

1. Add a test factory for discovery results
   - [ ] Given the 50 hand-built `DiscoveryResult`s in tests, when moved to the factory, then the suite passes with no change under `src/`.
2. Make `DiscoveredAsk` the result
   - [ ] Given asks with ids 0 and 2, when discovery runs, then the result holds one `DiscoveredAsk` per ask, in order, each with its ask.
   - [ ] Given two asks that search the same thing, when discovery runs, then one query is sent and both hold it.
   - [ ] Given an ask with no schema type, when discovery runs, then its object has no query and nothing found.
   - [ ] Given two entries with one ask id, when a `DiscoveryResult` is built, then it is rejected.
   - [ ] Given the four dicts, when this task is done, then they are read-only views of `asks`, so no reader changes yet.
3. Rank per ask
   - [ ] Given a sender's ranking, when `prioritise` runs, then each ask is reordered on its own object and `priority` is set.
4. Settlement and discovery take asks
   - [ ] Given settled and unsettled asks, when the turn runs, then discovery is handed the settled asks and no `Intent` is built for it.
5. The planner reads the per-ask object
   - [ ] Given `select(ask_id=2)`, when it runs, then it finds ask 2's object once and reads its candidates and ranking from it.
   - [ ] Given an ask id the turn does not have, when a tool is called, then the model is asked to retry.
   - [ ] Given discovered asks, when evidence is assembled, then each ask has its results, with the same source numbers as today.
6. The form step and the orchestrator drop the intent
   - [ ] Given a turn with a question and an answer, when it runs, then the reply is the same as today.
   - [ ] Given `TurnResult`, when this task is done, then `settled_intent` and `_settled_only` are gone.
7. A form submission passes its ask
   - [ ] Given a submitted form, when its provider is looked for, then discovery is handed one ask and no `Intent` is built.
8. Remove the dict views
   - [ ] Given `DiscoveryResult`, when this task is done, then the four dicts and `serves` are gone.
9. Docs
   - [ ] Given `TODO.md` and `docs/DSS_ARCHITECTURE.md`, when this ships, then the done item is removed, what is left is listed, and the architecture doc states the model.

## Test strategy

- Change a test before adding one. Most tests here change shape, not rule.
- Tier 1 (most): the two types, discovery, ranking, evidence assembly.
- Tier 3: the planner tools with a gap in the ask ids, and the mixed turn
  and form submission through the orchestrator, with the same replies.
- Tier 5: the live planner tests, once at the end. Not a merge gate.
