---
id: provider-invocation
domain: agriculture
description: Call providers to answer an ask.
tool_names: [select]
---
Read the capability's schema before calling `select`. Build `resourceAttributes`
from what the farmer actually said and the turn's location — never invent a
field the schema does not list. Call `select` once per ask that needs it, and
stop once every ask you can answer has been answered.
