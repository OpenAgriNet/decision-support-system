# ADR-0013: Send every model call through LiteLLM

- **Status:** PROPOSED. A working version was built and tested on a laptop.
- **Date:** 2026-09-23
- **Deciders:** OAN (OpenAgriNet) DPG architecture group
- **Issue:** OpenAgriNet/engineering-tracker#143
- **How to run it:** [`docs/RUNNING-GATEWAY.md`](../RUNNING-GATEWAY.md)

---

## 1. The problem

A turn asks a model four things: what the farmer wants, whether the question is
safe, which provider to call, and how to word the answer. Each of the four can
already use a different model.

Four things we could not do:

- **Use a model vendor we had not written code for.** Adding one meant a code
  change and a release.
- **Change a vendor password without redeploying the assistant.** The passwords
  sat in the assistant's own settings.
- **Survive a vendor outage.** One model per step, no backup.

## 2. The decision

Put one service between the assistant and every model vendor: **LiteLLM, run on
our own machines.** Every model call goes through it.

The assistant stops naming models. It names four labels instead —
`dss-intent`, `dss-moderation`, `dss-planner`, `dss-composer` — and one address,
`DSS_GATEWAY_URL`. The gateway decides what each label means.

There is no vendor anywhere in the assistant's settings, which is the point: a
setting that named one would send the call straight to that vendor, past the
spending limits and past the record of what the turn cost. The only way to reach
a model is through the gateway. Change the meaning there, and the next answer
uses the new model. Nothing restarts.

Vendor passwords move into the gateway, which keeps them encrypted in its own
database (§6). The assistant holds one password: its own key to the gateway.

We reach the gateway over plain HTTP, in the format most vendors already speak.
We do not install LiteLLM's software library. That keeps the door open.

## 3. What we proved

We built it and ran real farmer questions through it.

**It works.**

- A whole turn ran through the gateway with settings only. No code change was
  needed to get that far.
- Changing a step's model took effect on the very next answer, through the
  gateway's own interface, with nothing restarted. That is the mechanism §5
  relies on.
- Each step gets its own gateway key. A step cannot spend another step's money:
  we tried, and the gateway refused.
- A spending limit stopped the calls once it was reached, with a clear message.
- The gateway told us which vendor connection was broken. It found a wrong
  password in our own settings that we did not know about.

## 4. What changed in our code

**48 lines, in two files.** Nothing else in the assistant was touched.

- `adapters/llm/pydantic_ai_provider.py` — builds the connection to the gateway,
  and attaches the turn's tracking number to every call.
- `entrypoint/composition.py` — uses that connection when a gateway address is
  configured.

The rest is not code. It is deployment files: one for the gateway, one for its
filter, one for its settings, and a script that creates the four labels on a
fresh install.

**The tracking number needed real work.** The gateway does attach its records to
the turn's number when we send it one — but the standard tool for sending it does
not reach the model connection. We attach it by hand instead. Without this, one
turn became seven unrelated records.

## 5. Where the model list lives

**In version control, and nowhere else.** The gateway has an admin screen that
edits the running configuration. We turn it off.

The list of which model each step uses lives in the Helm chart, in
`OpenAgriNet/helmcharts`. Changing a model is a change to a values file: a pull
request, a review, a merge. Nothing is typed into a screen, and the repository
always describes what is running.

**A change still takes effect without restarting anything.** A job runs after
every deployment. It reads the list from the chart and tells the gateway, through
its own interface, what the list now says. The gateway picks the change up
immediately. Neither the gateway nor the assistant restarts.

That job is also what makes the file authoritative rather than advisory. It works
in both directions: a model in the file is created or updated, and a model in the
gateway that is not in the file is removed. Without that second half, deleting a
model from the file would leave it serving, and the file would slowly stop being
true.

**Three doors, all closed but one.** The admin screen is off. A hand-edited
setting in the cluster changes nothing, because nothing watches it — only a
deployment runs the job. What is left is the pull request.

**What this costs.** A model change is a deployment, so it takes minutes rather
than seconds, and it needs someone who can merge. We think that is the right
price: this is the setting that decides what every farmer's answer is written by,
and what it costs.

**We are not locked in.** The gateway reads a list from a file as well as from
its own store, so a later decision to hold the list in the file and restart the
gateway on change is available. It costs a restart per change, which is why it is
not what we do.

## 6. Where the vendor keys live

Not in a file. The gateway stores them in its own database, encrypted, and a
model refers to one by name.

We create a credential once per vendor account:

```
azure-oan  ->  { api_key: <encrypted>, api_base: <encrypted> }
```

The model then holds no key at all — only the name `azure-oan`. Anyone reading
the database sees encrypted text, not the key.

**Why encrypt it.** Putting every vendor key in one place makes that place worth
stealing. A copy of the database leaves the machine every time someone takes a
backup, restores it somewhere to test something, or gives a colleague read access
to look at spending. Encrypting the keys means those copies carry nothing usable,
and it lets someone read the spending tables without being handed the vendor
accounts.

It is not total protection. The key that unlocks it sits in the gateway's own
settings, so anyone who controls the machine can read both. What it defends
against is the copy that travels — a backup, a dump, a test restore, a spare
pair of eyes on the database.

**A credential is per vendor account, not per model.** All five of our models
share `azure-oan`. A second credential is only needed when a second vendor
arrives, and every model on that vendor then shares it too.

**What this leaves in the deployment file:** two secrets, both belonging to the
gateway itself — the master password and the encryption key. Vendor keys are no
longer there, and were never in the assistant.

**A key is added the same way as everything else.** It is named in the chart and
read from a Kubernetes secret by the same job that applies the model list. The
key itself never appears in a repository, and nobody types it into a screen.

### The trap we fell into

The encryption key is `LITELLM_SALT_KEY`. We set it *after* some models had
already been saved. Everything saved before that point could no longer be
decrypted, so the gateway began sending nonsense to Azure, which replied
*"invalid subscription key or wrong API endpoint"*. Nothing warned us. We had to
delete those models and create them again.

Two rules follow, and they are not optional:

- **Set the encryption key before saving anything, and never change it.** The
  deployment file now refuses to start the gateway without it.
- **Keep a copy of it wherever the other secrets are kept.** Lose it and every
  vendor key has to be typed in again.

One more consequence: a backup of this database now contains vendor keys, albeit
encrypted. Treat the backup as a secret.

Reading keys straight from a company secret store, such as Vault, is a paid
feature. We do not use it.

## 7. What this costs us

- **Everything now depends on one service.** If the gateway is down, every turn
  fails. There is no quiet fallback to calling the vendor directly, on purpose: a
  fallback like that would also skip the spending limit, which is the reason the
  gateway exists.
- **Another database to run, and now a sensitive one.** The gateway keeps its
  keys, its limits, its spending history, its model list and its vendor
  credentials in its own Postgres. That is three databases in this deployment,
  counting the two Langfuse needs, and this one holds secrets.
- **Reported cost is only as good as the price list**, and a missing price is
  worse than an old one: it reads as free and switches off the limit.
- **One extra hop on every call**, including the first words of the farmer's
  answer.
- **Twelve extra recorded steps per turn**, after filtering.

Against that: adding a vendor is a form, vendor passwords leave the assistant,
spending becomes a number per step, and a failed vendor can fall back to another.

## 8. Leaving LiteLLM later

Cheap, and we should keep it that way.

- **The call itself costs nothing to move.** It is the common format. Other
  gateways speak it. Our 48 lines and our four settings would not change.
- **The model list is a morning's work** — same idea, different file format.
- **The spending history stays behind.** It is stored in LiteLLM's own shape. A
  new gateway starts its books empty.
- **Keys and limits are recreated by hand**, and every deployment needs the new
  ones. This needs a written procedure before it happens.
- **One small piece is LiteLLM-specific:** the filter names LiteLLM's internal
  steps. Any other gateway would need its own.

**The rule that keeps it cheap:** if the assistant would have to send something
that only LiteLLM understands, it belongs in the gateway's settings instead.
Today it sends nothing of the sort.

What we cannot undo cheaply: the assistant can no longer reach a model without a
gateway of some kind. Going back means restoring code this decision removes. We
accept that.

## 9. Still open

- Joining cost to the turn. Three options: teach Langfuse our labels and their
  prices, make the gateway report the real model name, or read cost from the
  gateway and leave Langfuse the story of the turn.
- The four per-step keys, which need the small code change in §3.
- Warning when a step is close to its limit, rather than only stopping it.
- Whether a vendor other than Azure behaves the same. We only had one working
  vendor password, so this is still the supplier's word, not ours.
- How much slower a call is through the gateway. Not measured.
- Who keeps the price list correct, now that limits depend on it.
- Where the encryption key is kept, and who can read it.
- How quickly a model change can be reviewed and merged when something is going
  wrong in production, now that a change is a pull request (§5).
