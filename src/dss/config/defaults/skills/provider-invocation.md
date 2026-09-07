---
id: provider-invocation
domain: agriculture
description: Call providers to answer an ask.
tool_names: [describe_capability, select]
---
For each ask that needs a provider, call `describe_capability` first to see
the candidates and which fields you may set. Then call `select` once per ask,
using only fields `describe_capability` told you are valid — never invent a
field. Build the fields you do set from what the farmer actually said. Stop
once every ask you can answer has been answered.
