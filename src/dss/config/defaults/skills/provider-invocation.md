---
id: provider-invocation
domain: agriculture
description: Call providers to answer an ask.
tool_names: [describe_capability, select]
---
For each ask that needs a provider, call `describe_capability` first to see
the candidates and which fields you may set. Then call `select` once per ask,
using only fields `describe_capability` told you are valid — never invent a
field. Build the fields you do set from what the farmer actually said.

Fill every field the farmer's words or the turn's location can answer, not
just the minimum. A narrow query returns fewer, more relevant results; an
empty one returns everything the provider has. If you can tell what kind of
thing the farmer wants (a facility type, a topic, a commodity), say so —
don't leave it out just because the field wasn't marked required.

Stop once every ask you can answer has been answered.
