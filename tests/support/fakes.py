"""Test doubles.

Hand-written, not `MagicMock`: a mock answers a method you renamed in the
Protocol, so the test keeps passing after the contract moved. A stub class
fails, which is the point of having a contract at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from dss.core.enrichment.models import Scheme
from dss.core.enrichment.normalize import alias_key
from dss.core.planner.models import Skill
from dss.core.provider_preferences.models import PreferenceBook, topic_key
from dss.core.shared.errors import ProviderUnavailableError
from dss.core.shared.models import Geometry, TurnContext, TurnEvent, UserTurn
from dss.ports.area_lookup import AreaMatch
from dss.ports.prompts import PromptIdentifier


class FakeSchemeCatalog:
    """A `SchemeCatalog` over an alias-text → scheme-name mapping.

    Keys are normalized with the real `alias_key`, so a fake cannot pass a
    lookup the CSV adapter could never serve. Default-empty is the
    unmounted-catalog case.
    """

    def __init__(self, schemes: dict[str, str] | None = None) -> None:
        self._aliases = {
            alias_key(alias): Scheme(code=alias_key(alias), name=name)
            for alias, name in (schemes or {}).items()
        }

    def aliases(self):
        return self._aliases


class FakeProviderPreferences:
    """A `ProviderPreferences` over rankings written as `{client: {topic: [ids]}}`.

    Default-empty is the unmounted case: no client has a ranking, so every ask
    keeps whatever discovery found. Pass `default=` for the layer an unknown
    client falls back to.
    """

    def __init__(
        self,
        clients: dict[str, dict[str, list[str]]] | None = None,
        *,
        default: dict[str, list[str]] | None = None,
    ) -> None:
        self._book = PreferenceBook(
            default=_ranked(default or {}),
            clients={
                client: _ranked(rankings)
                for client, rankings in (clients or {}).items()
            },
        )
        self.asked: list[str | None] = []

    async def for_client(self, client_id: str | None):
        self.asked.append(client_id)
        return self._book.for_client(client_id)


def _ranked(rankings: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    """Topic names keyed the way the real loader keys them, so a fake cannot
    answer a lookup the YAML adapter could not."""

    return {topic_key(topic): tuple(providers) for topic, providers in rankings.items()}


NO_PREFERENCES = FakeProviderPreferences()


class FakeRunner:
    """Yields the events it was handed, optionally failing part-way.

    `fail_after=n` raises once `n` events have been yielded, which is how the
    transport's mid-stream failure path is reached without a real dependency.
    """

    def __init__(
        self, events: Sequence[TurnEvent], *, fail_after: int | None = None
    ) -> None:
        self.events = list(events)
        self.fail_after = fail_after
        self.calls: list[tuple[UserTurn, TurnContext]] = []

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        self.calls.append((turn, ctx))
        for index, event in enumerate(self.events):
            if index == self.fail_after:
                raise ProviderUnavailableError("fake runner asked to fail")
            yield event


class FakeTelemetrySink:
    """Records stages. `fail=True` makes every write raise, which is how the
    "an optional sink never fails a turn" rule gets exercised."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.stages: list[tuple[str, str]] = []

    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None:
        if self.fail:
            raise RuntimeError("telemetry backend unreachable")
        self.stages.append((name, outcome))


class FakeTurnSink:
    """Records the turn. `fail_on` names the call that raises, which is how the
    "the audit trail is required" rule gets exercised."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.opened_with: list[UserTurn] = []
        self.closed_with: list[object] = []

    def opened(self, ctx: TurnContext, turn: UserTurn) -> None:
        if self.fail_on == "opened":
            raise RuntimeError("turn store unreachable")
        self.opened_with.append(turn)

    def closed(self, ctx: TurnContext, finished: object) -> None:
        if self.fail_on == "closed":
            raise RuntimeError("turn store unreachable")
        self.closed_with.append(finished)


class FakePromptProvider:
    """A `PromptProvider` that records what was asked and answers with a
    legible stand-in prompt.

    The returned string carries the identifier, the language, and every
    placeholder's rendered value, so a tier-1 test can assert "the history
    reached the prompt" without reading the real template — the template's own
    text is config, pinned by `tests/unit/config/test_shipped_prompts.py`.
    """

    def __init__(self, skills: Sequence[Skill] = ()) -> None:
        self._skills = tuple(skills)
        self.calls: list[tuple[PromptIdentifier, str, dict[str, Any]]] = []

    def get_prompt(
        self,
        prompt_identifier: PromptIdentifier,
        lang: str,
        placeholder_values: Mapping[str, Any] | None = None,
    ) -> str:
        values = dict(placeholder_values or {})
        self.calls.append((prompt_identifier, lang, values))
        lines = [f"[{prompt_identifier.value} prompt, lang={lang}]"]
        for key, value in values.items():
            rendered = (
                "\n".join(f"  {m.role}: {m.text}" for m in value)
                if key == "history"
                else str(value)
            )
            lines.append(f"{key}: {rendered}")
        return "\n".join(lines)

    def get_skills(
        self, prompt_identifier: PromptIdentifier, lang: str
    ) -> tuple[Skill, ...]:
        return self._skills


class FakeLocalizer:
    """A `LocalizeText` double. Passthrough by default so existing answer-text
    assertions hold; `mark=True` wraps the text so a test can see localization
    happened and to which language. Records every call either way."""

    def __init__(self, *, mark: bool = False) -> None:
        self.mark = mark
        self.calls: list[tuple[str, str]] = []  # (text, target_lang)

    async def __call__(self, text: str, *, turn: UserTurn) -> str:
        self.calls.append((text, turn.target_lang))
        return f"[{turn.target_lang}] {text}" if self.mark else text


class FakeAreaLookup:
    """An `AreaLookup` over a name → matches dict, for tests that need a place
    name to resolve (or deliberately not to).

    Defaults to empty, which is the honest double for the many discovery tests
    that pass an explicit geometry or none at all: they never consult it, and an
    empty index makes an accidental consultation resolve to nothing rather than
    to a coincidentally-correct point.
    """

    def __init__(
        self,
        matches: dict[str, list[AreaMatch]] | None = None,
        *,
        nearest: AreaMatch | None = None,
    ) -> None:
        self._matches = matches or {}
        self._nearest = nearest
        self.calls: list[tuple[str, str | None]] = []
        self.nearest_max_km: list[float] = []

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls.append((name, region))
        found = self._matches.get(" ".join(name.split()).lower(), [])
        if region is None:
            return list(found)
        return [match for match in found if match.region == region]

    async def nearest(self, point: Geometry, max_km: float) -> AreaMatch | None:
        self.nearest_max_km.append(max_km)
        return self._nearest
