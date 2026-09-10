"""Test doubles.

Hand-written, not `MagicMock`: a mock answers a method you renamed in the
Protocol, so the test keeps passing after the contract moved. A stub class
fails, which is the point of having a contract at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from dss.core.enrichment.models import Scheme
from dss.core.enrichment.normalize import alias_key
from dss.core.shared.errors import ProviderUnavailable
from dss.core.shared.models import TurnContext, TurnEvent, UserTurn
from dss.ports.area_lookup import AreaMatch


class FakeSchemeCatalog:
    """Satisfies `ports.scheme_catalog.SchemeCatalog` from a plain mapping of
    alias text to scheme name.

    Normalizes its keys with the real `alias_key`, because a fake that indexed
    them differently would let a test pass on a lookup the adapter could never
    serve. Default-empty, which is the unmounted-catalog case.
    """

    def __init__(self, schemes: dict[str, str] | None = None) -> None:
        self._aliases = {
            alias_key(alias): Scheme(code=alias_key(alias), name=name)
            for alias, name in (schemes or {}).items()
        }

    def aliases(self):
        return self._aliases


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
                raise ProviderUnavailable("fake runner asked to fail")
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


class FakeAreaLookup:
    """An `AreaLookup` over a name → matches dict, for tests that need a place
    name to resolve (or deliberately not to).

    Defaults to empty, which is the honest double for the many discovery tests
    that pass an explicit geometry or none at all: they never consult it, and an
    empty index makes an accidental consultation resolve to nothing rather than
    to a coincidentally-correct point.
    """

    def __init__(self, matches: dict[str, list[AreaMatch]] | None = None) -> None:
        self._matches = matches or {}
        self.calls: list[tuple[str, str | None]] = []

    def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls.append((name, region))
        found = self._matches.get(" ".join(name.split()).lower(), [])
        if region is None:
            return list(found)
        return [match for match in found if match.region == region]
