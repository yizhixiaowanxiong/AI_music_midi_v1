"""Track scheduler: run musician agents concurrently with timeout protection."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Callable, Dict, Tuple

from agents.base_musician_agent import BaseMusicianAgent
from agents.bass_agent import BassAgent
from agents.drums_agent import DrumsAgent
from agents.fx_agent import FxAgent
from agents.harmony_agent import HarmonyAgent
from agents.melody_agent import MelodyAgent
from agents.musician_llm_agent import pop_musician_log_scope, push_musician_log_scope
from observability.metrics import (
    AGENT_CACHE_HIT_TOTAL,
    AGENT_CACHE_MISS_TOTAL,
    RUNNER_QUEUE_WAIT_GLOBAL_SECONDS,
    RUNNER_QUEUE_WAIT_RUN_SECONDS,
    TRACK_TIMEOUT_TOTAL,
)
from schema.arrangement import GeneratedTrack, TrackContext, TrackGenRequest
from schema.base import AgentRoutingRole
from utils.context_tools import inject_context_if_needed


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, min_value: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, value)


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return str(default).lower()
    if raw in choices:
        return raw
    return str(default).lower()


AGENT_FACTORY: Dict[AgentRoutingRole, Callable[[], BaseMusicianAgent]] = {
    AgentRoutingRole.PERCUSSION: DrumsAgent,
    AgentRoutingRole.BASS: BassAgent,
    AgentRoutingRole.MELODY: MelodyAgent,
    AgentRoutingRole.HARMONY: HarmonyAgent,
    AgentRoutingRole.FX: FxAgent,
}

_CONCURRENCY = _env_int("TRACK_CONCURRENCY", default=4, min_value=1, max_value=16)
_SEM = asyncio.Semaphore(_CONCURRENCY)
_RUN_CONCURRENCY = _env_int(
    "TRACK_CONCURRENCY_PER_RUN",
    default=_CONCURRENCY,
    min_value=1,
    max_value=16,
)
_AGENT_CACHE_SCOPE = _env_choice("AGENT_CACHE_SCOPE", default="run", choices=("run", "process"))
_TRACK_TIMEOUT_SEC = _env_float("TRACK_TIMEOUT_SEC", default=90.0, min_value=0.0)
_TRACK_TIMEOUT_SOFT_CAP_SEC = _env_float("TRACK_TIMEOUT_SOFT_CAP_SEC", default=90.0, min_value=0.0)
if _TRACK_TIMEOUT_SOFT_CAP_SEC > 0:
    _TRACK_TIMEOUT_SEC = min(_TRACK_TIMEOUT_SEC, _TRACK_TIMEOUT_SOFT_CAP_SEC)

_PROCESS_SCOPE_ID = "__process__"

# (scope_id, role) -> (factory, instance)
_AGENT_CACHE: Dict[Tuple[str, AgentRoutingRole], Tuple[Callable[[], BaseMusicianAgent], BaseMusicianAgent]] = {}

# scope_id -> semaphore
_RUN_SCOPE_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}


def _resolve_scope_id(run_scope_id: str | None) -> str:
    text = str(run_scope_id or "").strip()
    if _AGENT_CACHE_SCOPE == "run" and text:
        return f"run:{text}"
    return _PROCESS_SCOPE_ID


def _scope_label(scope_id: str) -> str:
    if str(scope_id or "").startswith("run:"):
        return "run"
    return "process"


def _role_label(role: AgentRoutingRole | str | None) -> str:
    if isinstance(role, AgentRoutingRole):
        return role.value
    return str(role or "unknown").strip().lower() or "unknown"


def _get_scope_semaphore(scope_id: str) -> asyncio.Semaphore:
    sem = _RUN_SCOPE_SEMAPHORES.get(scope_id)
    if sem is not None:
        return sem
    sem = asyncio.Semaphore(_RUN_CONCURRENCY)
    _RUN_SCOPE_SEMAPHORES[scope_id] = sem
    return sem


def _clear_agent_cache(scope_id: str | None = None) -> None:
    """Clear cached agent instances (used by tests/reload paths)."""
    if scope_id is None:
        _AGENT_CACHE.clear()
        _RUN_SCOPE_SEMAPHORES.clear()
        return

    keys = [key for key in _AGENT_CACHE.keys() if key[0] == scope_id]
    for key in keys:
        _AGENT_CACHE.pop(key, None)
    _RUN_SCOPE_SEMAPHORES.pop(scope_id, None)


def clear_run_runtime(run_scope_id: str | None) -> None:
    """Clear per-run cached agent instances and semaphores."""
    scope_id = _resolve_scope_id(run_scope_id)
    _clear_agent_cache(scope_id=scope_id)


def _get_or_create_agent(
    scope_id: str,
    role: AgentRoutingRole,
    factory: Callable[[], BaseMusicianAgent],
) -> BaseMusicianAgent:
    scope = _scope_label(scope_id)
    cache_key = (scope_id, role)
    cached = _AGENT_CACHE.get(cache_key)
    if cached and cached[0] is factory:
        AGENT_CACHE_HIT_TOTAL.labels(scope=scope).inc()
        return cached[1]

    agent = factory()
    _AGENT_CACHE[cache_key] = (factory, agent)
    AGENT_CACHE_MISS_TOTAL.labels(scope=scope).inc()
    return agent


async def _call_generate(agent: BaseMusicianAgent, req: TrackGenRequest) -> GeneratedTrack:
    fn = getattr(agent, "generate", None)
    if fn is None:
        raise RuntimeError(f"{agent.__class__.__name__} missing async generate(req)")

    out = fn(req)
    if not inspect.isawaitable(out):
        raise RuntimeError(f"{agent.__class__.__name__}.generate must be async")
    return await out


async def run_one_track(
    req: TrackGenRequest,
    runtime_context: TrackContext,
    run_scope_id: str | None = None,
    session_scope_id: str | None = None,
) -> GeneratedTrack:
    scope_id = _resolve_scope_id(run_scope_id)
    role = getattr(req, "instrument", None)
    factory = AGENT_FACTORY.get(role)
    if not factory:
        return GeneratedTrack(
            track_key=getattr(req, "track_key", "unknown"),
            instrument=role,
            section_name=getattr(req, "section_name", "Unknown"),
            notes=[],
            error=f"No agent found for role={role}",
        )

    req_injected = inject_context_if_needed(req, runtime_context)
    track_key = getattr(req_injected, "track_key", "unknown")

    try:
        agent = _get_or_create_agent(scope_id, role, factory)
    except Exception as exc:
        return GeneratedTrack(
            track_key=track_key,
            instrument=role,
            section_name=getattr(req_injected, "section_name", "Unknown"),
            notes=[],
            error=f"Failed to create agent for role={role}: {exc}",
        )

    try:
        log_tokens = push_musician_log_scope(
            session_id=str(session_scope_id or ""),
            run_id=str(run_scope_id or ""),
            track_key=str(track_key),
        )
        global_wait_started = time.perf_counter()
        async with _SEM:
            RUNNER_QUEUE_WAIT_GLOBAL_SECONDS.observe(max(0.0, time.perf_counter() - global_wait_started))
            run_wait_started = time.perf_counter()
            async with _get_scope_semaphore(scope_id):
                RUNNER_QUEUE_WAIT_RUN_SECONDS.observe(max(0.0, time.perf_counter() - run_wait_started))
                if _TRACK_TIMEOUT_SEC > 0:
                    return await asyncio.wait_for(
                        _call_generate(agent, req_injected),
                        timeout=_TRACK_TIMEOUT_SEC,
                    )
                return await _call_generate(agent, req_injected)
    except asyncio.TimeoutError:
        TRACK_TIMEOUT_TOTAL.labels(role=_role_label(role)).inc()
        return GeneratedTrack(
            track_key=track_key,
            instrument=role,
            section_name=getattr(req_injected, "section_name", "Unknown"),
            notes=[],
            error=f"timeout after {_TRACK_TIMEOUT_SEC:.1f}s",
        )
    except Exception as exc:
        return GeneratedTrack(
            track_key=track_key,
            instrument=role,
            section_name=getattr(req_injected, "section_name", "Unknown"),
            notes=[],
            error=str(exc),
        )
    finally:
        if "log_tokens" in locals():
            pop_musician_log_scope(log_tokens)
