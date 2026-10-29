---
id: provider-action
domain: agriculture
description: File a submitted form with a provider, or check a case's status with it.
tool_names: [describe_capability, support, status]
input_kind: form
---
An ask marked "files the farmer's form" is answered by the `support` tool,
which sends the form's values. Call `describe_capability` first to see the
providers that can take the form. Pick one, and call `support` once with its
resource_id.

You author no field. The form already holds every value, and the tool builds
the call from it; there is nothing for you to fill in or to check.

If `support` reports that the form was not filed, do not call it again for
that ask. Report that it was not filed and stop. Once it reports a filing,
stop.

An ask marked "checks the status with the farmer's form" is answered by the
`status` tool instead, the same way: `describe_capability`, pick one provider,
call `status` once. If it reports that the status was not checked, do not
call it again; report that and stop. Once it reports the case, stop.
