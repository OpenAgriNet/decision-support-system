from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import NewType, Protocol

import anyio

from dss.core.forms.models import FormRecords
from dss.core.shared.models import TurnContext
from dss.core.shared.session_state import SessionState
from dss.ports.key_value_store import KeyValueStore, LockAcquireTimeoutError

logger = logging.getLogger(__name__)

SessionKey = NewType("SessionKey", str)

# Our numbers; tune with the provider docs.
_SESSION_TTL_SECONDS = 180
# Longer than a form turn, so a slow turn does not lose its lock mid-way.
_SESSION_LOCK_LEASE_SECONDS = 120
# Past it the turn is told another message is still being handled.
_SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS = 30
# The save and release run shielded, so nothing else can stop a hung store.
_SESSION_WRITE_DEADLINE_SECONDS = 3


class _LockOutcome(Enum):
    HELD = "held"
    BUSY = "busy"
    UNREACHABLE = "unreachable"


class Session:
    def __init__(self, state: SessionState, *, is_kept: bool) -> None:
        self._loaded = state
        self._state = state
        self._is_kept = is_kept

    @property
    def state(self) -> SessionState:
        return self._state

    # False when nothing saves the state, so it ends with the turn.
    @property
    def is_kept(self) -> bool:
        return self._is_kept

    @property
    def forms(self) -> FormRecords:
        return self._state.forms

    def update_forms(self, forms: FormRecords) -> None:
        # Copied, not rebuilt: a feature writes its own section and no other.
        self._state = self._state.model_copy(update={"forms": forms})

    @property
    def changed(self) -> bool:
        return self._state != self._loaded


@dataclass(frozen=True)
class SessionBusy:
    pass


class Sessions(Protocol):
    def open(
        self, ctx: TurnContext
    ) -> AbstractAsyncContextManager[Session | SessionBusy]: ...


def _one_turn_session() -> Session:
    return Session(SessionState(), is_kept=False)


def session_key_for(ctx: TurnContext) -> SessionKey:
    # Per sender: two callers may pick the same session id.
    return SessionKey(f"{ctx.sender_id}:{ctx.session_id}")


class NoSessionStore:
    @asynccontextmanager
    async def open(self, ctx: TurnContext) -> AsyncGenerator[Session | SessionBusy]:
        yield _one_turn_session()


class SessionStore:
    def __init__(
        self,
        store: KeyValueStore,
        *,
        acquire_timeout_seconds: float = _SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS,
        write_deadline_seconds: float = _SESSION_WRITE_DEADLINE_SECONDS,
    ) -> None:
        self._store = store
        self._acquire_timeout_seconds = acquire_timeout_seconds
        self._write_deadline_seconds = write_deadline_seconds

    @asynccontextmanager
    async def open(self, ctx: TurnContext) -> AsyncGenerator[Session | SessionBusy]:
        key = session_key_for(ctx)
        async with self._held_lock(key, ctx) as lock:
            match lock:
                case _LockOutcome.BUSY:
                    yield SessionBusy()
                case _LockOutcome.UNREACHABLE:
                    # A store that is down costs the session, never the turn.
                    yield _one_turn_session()
                case _LockOutcome.HELD:
                    # Closed before the lock: the save runs while it is held.
                    async with self._loaded_session(key, ctx) as session:
                        yield session

    @asynccontextmanager
    async def _held_lock(
        self, key: SessionKey, ctx: TurnContext
    ) -> AsyncGenerator[_LockOutcome]:
        lock = AsyncExitStack()
        # Yielded outside _acquire's except blocks, so a turn's own error is
        # not chained to the store's.
        outcome = await self._acquire(lock, key, ctx)
        try:
            yield outcome
        finally:
            # Shielded: a closed stream is closed inside a cancelled task.
            with anyio.CancelScope(shield=True):
                await self._release(lock, ctx)

    @asynccontextmanager
    async def _loaded_session(
        self, key: SessionKey, ctx: TurnContext
    ) -> AsyncGenerator[Session]:
        stored = await self._read_state(key, ctx)
        session = Session(
            SessionState() if stored is None else stored, is_kept=stored is not None
        )
        try:
            yield session
        finally:
            # State that was not read is not known to be empty: saving
            # would overwrite what is stored.
            if stored is not None:
                with anyio.CancelScope(shield=True):
                    await self._save_if_changed(key, session, ctx)

    async def _acquire(
        self, lock: AsyncExitStack, key: SessionKey, ctx: TurnContext
    ) -> _LockOutcome:
        try:
            await lock.enter_async_context(self.lock(key))
        except LockAcquireTimeoutError:
            return _LockOutcome.BUSY
        except Exception as error:  # noqa: BLE001 - the port names no error type
            _log_store_failure(ctx, step="lock", reason=type(error).__name__)
            return _LockOutcome.UNREACHABLE
        return _LockOutcome.HELD

    def lock(self, key: SessionKey) -> AbstractAsyncContextManager[None]:
        return self._store.lock(
            key,
            lease_seconds=_SESSION_LOCK_LEASE_SECONDS,
            acquire_timeout_seconds=self._acquire_timeout_seconds,
        )

    async def _save_if_changed(
        self, key: SessionKey, session: Session, ctx: TurnContext
    ) -> None:
        if not session.changed:
            return
        # The answer is already sent; a failed save must not replace it.
        try:
            with anyio.fail_after(self._write_deadline_seconds):
                await self._store.set(
                    key,
                    session.state.model_dump_json(),
                    ttl_seconds=_SESSION_TTL_SECONDS,
                )
        except Exception as error:  # noqa: BLE001 - the port names no error type
            _log_store_failure(ctx, step="save", reason=type(error).__name__)

    async def _release(self, lock: AsyncExitStack, ctx: TurnContext) -> None:
        try:
            with anyio.fail_after(self._write_deadline_seconds):
                await lock.aclose()
        except Exception as error:  # noqa: BLE001 - the port names no error type
            _log_store_failure(ctx, step="release", reason=type(error).__name__)

    # None when the read failed. Placeholder: whether a failed read rejects
    # the turn is an open business question; until then it starts from nothing.
    async def _read_state(
        self, key: SessionKey, ctx: TurnContext
    ) -> SessionState | None:
        try:
            stored = await self._store.get(key)
            if stored is None:
                return SessionState()
            return SessionState.model_validate_json(stored)
        except Exception as error:  # noqa: BLE001 - the port names no error type
            _log_store_failure(ctx, step="read", reason=type(error).__name__)
            return None


# The error's type only: a parse error quotes stored values, a farmer's too.
def _log_store_failure(ctx: TurnContext, *, step: str, reason: str) -> None:
    logger.warning("request_id=%s session_%s_failed=%s", ctx.trace_id, step, reason)
