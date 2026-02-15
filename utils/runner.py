"""Track scheduler: run musician agents concurrently with timeout protection."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Callable, Dict, Tuple

from agents.base_musician_agent import BaseMusicianAgent
from agents.bass_agent import BassAgent
from agents.drums_agent import DrumsAgent
from agents.fx_agent import FxAgent
from agents.harmony_agent import HarmonyAgent
from agents.melody_agent import MelodyAgent
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


AGENT_FACTORY: Dict[AgentRoutingRole, Callable[[], BaseMusicianAgent]] = {
    AgentRoutingRole.PERCUSSION: DrumsAgent,
    AgentRoutingRole.BASS: BassAgent,
    AgentRoutingRole.MELODY: MelodyAgent,
    AgentRoutingRole.HARMONY: HarmonyAgent,
    AgentRoutingRole.FX: FxAgent,
}

_CONCURRENCY = _env_int("TRACK_CONCURRENCY", default=4, min_value=1, max_value=16)
_SEM = asyncio.Semaphore(_CONCURRENCY)
_TRACK_TIMEOUT_SEC = _env_float("TRACK_TIMEOUT_SEC", default=120.0, min_value=0.0)

# role -> (factory, instance)
_AGENT_CACHE: Dict[AgentRoutingRole, Tuple[Callable[[], BaseMusicianAgent], BaseMusicianAgent]] = {}


def _clear_agent_cache() -> None:
    """Clear cached agent instances (used by tests/reload paths)."""
    _AGENT_CACHE.clear()


def _get_or_create_agent(
    role: AgentRoutingRole,
    factory: Callable[[], BaseMusicianAgent],
) -> BaseMusicianAgent:
    cached = _AGENT_CACHE.get(role)
    if cached and cached[0] is factory:
        return cached[1]

    agent = factory()
    _AGENT_CACHE[role] = (factory, agent)
    return agent


async def _call_generate(agent: BaseMusicianAgent, req: TrackGenRequest) -> GeneratedTrack:
    fn = getattr(agent, "generate", None)
    if fn is None:
        raise RuntimeError(f"{agent.__class__.__name__} missing async generate(req)")

    out = fn(req)
    if not inspect.isawaitable(out):
        raise RuntimeError(f"{agent.__class__.__name__}.generate must be async")
    return await out


async def run_one_track(req: TrackGenRequest, runtime_context: TrackContext) -> GeneratedTrack:
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
        agent = _get_or_create_agent(role, factory)
    except Exception as exc:
        return GeneratedTrack(
            track_key=track_key,
            instrument=role,
            section_name=getattr(req_injected, "section_name", "Unknown"),
            notes=[],
            error=f"Failed to create agent for role={role}: {exc}",
        )

    try:
        async with _SEM:
            if _TRACK_TIMEOUT_SEC > 0:
                return await asyncio.wait_for(
                    _call_generate(agent, req_injected),
                    timeout=_TRACK_TIMEOUT_SEC,
                )
            return await _call_generate(agent, req_injected)
    except asyncio.TimeoutError:
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
