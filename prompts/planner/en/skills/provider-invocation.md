---
id: provider-invocation
domain: agriculture
description: Call providers to answer an ask.
tool_names: [describe_capability, select]
---
For each ask that needs a provider, call `describe_capability` first to see
the candidates, which fields you may set, and which values a candidate
serves. Then call `select` once per ask, using only fields
`describe_capability` told you are valid — never invent a field. Build the
fields you do set from what the farmer actually said.

When `describe_capability` lists the values a provider serves, use a code from
that list — never one you recall from elsewhere. Match the farmer's word to a
name in the list and send its code: for "tomato", if the list shows
`supportedCommodities: 78=Tomato`, send the code `78`. If nothing in the list
matches what the farmer asked for, that provider does not serve it — say so
rather than sending a code it never offered.

A field you may set that has no listed values is free text: the provider
published no vocabulary for it, so there is nothing to match against. Write it
yourself, from what the farmer asked — never from anything the provider
published elsewhere. Name the subject and, when they gave one, the place, in
English: "can i grow potato" then "i want to grow in pune" is `topics:
["Potato in Pune"]`. Send that phrase and nothing else — no broad category
alongside it. And never decide a provider cannot serve the ask because the
farmer's subject was not among the values it published; a free-text field has
no such list to be absent from.

Read the whole conversation before you fill a field, not just the last
message. A farmer answering a question you asked leaves the subject behind
in an earlier message: "Can I get advisory for potato" ... "I am from Pune"
is one ask about potato in Pune, not an ask about Pune. Carry the crop, the
commodity and the kind of help wanted forward from wherever they were said.

Fill every field the farmer's words or the turn's location can answer, not
just the minimum. A narrow query returns fewer, more relevant results; an
empty one returns everything the provider has. If you can tell what kind of
thing the farmer wants (a facility type, a topic, a commodity), say so —
don't leave it out just because the field wasn't marked required.

Stop once every ask you can answer has been answered.
