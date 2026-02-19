"""Graph nodes for concept/blueprint generation, review gates, and production."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from agents.concept_agent import ConceptAgent
from agents.director_agent import DirectorAgent
from graph.state import MusicState
from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept
from utils.context_tools import build_blueprint_summaries, build_concept_summaries
from utils.dispatch import TrackDispatcher
from utils.quality_gate import (
    QG_MAX_RETRIES,
    fill_gaps_by_pattern_repeat,
    format_issue_feedback,
    validate_all_tracks,
)
from utils.runner import run_one_track

logger = logging.getLogger(__name__)

_ACTION_ACCEPT = "accept"
_ACTION_EDIT = "edit"
_ACTION_REGENERATE = "regenerate"
_ACTION_ABORT = "abort"

_ACTION_ALIASES = {
    "approve": _ACTION_ACCEPT,
    "approved": _ACTION_ACCEPT,
    "ok": _ACTION_ACCEPT,
    "yes": _ACTION_ACCEPT,
    "revise": _ACTION_EDIT,
    "update": _ACTION_EDIT,
    "redo": _ACTION_REGENERATE,
    "regen": _ACTION_REGENERATE,
    "rerun": _ACTION_REGENERATE,
    "stop": _ACTION_ABORT,
    "cancel": _ACTION_ABORT,
}


def _text(value: Any, max_chars: int = 0) -> str:
    text = str(value or "").strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_action(value: Any) -> str:
    raw = _text(value).lower()
    if not raw:
        return ""
    if raw in (_ACTION_ACCEPT, _ACTION_EDIT, _ACTION_REGENERATE, _ACTION_ABORT):
        return raw
    return _ACTION_ALIASES.get(raw, "")


def _as_concept(value: Any) -> SongConcept | None:
    if isinstance(value, SongConcept):
        return value
    if isinstance(value, dict):
        try:
            return SongConcept.model_validate(value)
        except Exception:
            return None
    return None


def _as_blueprint(value: Any) -> SongBlueprint | None:
    if isinstance(value, SongBlueprint):
        return value
    if isinstance(value, dict):
        try:
            return SongBlueprint.model_validate(value)
        except Exception:
            return None
    return None


def _legacy_feedback(state: MusicState) -> str:
    return _text(state.get("user_feedback", ""), 1200)


def _review_payload(state: MusicState, target: str) -> Dict[str, Any]:
    raw = state.get(target)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _feedback_from_state(state: MusicState, target: str) -> str:
    payload = _review_payload(state, target)
    feedback = _text(payload.get("feedback", ""), 1200)
    if feedback:
        return feedback
    return _legacy_feedback(state)


def _action_from_state(state: MusicState, target: str) -> str:
    payload = _review_payload(state, target)
    action = _normalize_action(payload.get("action"))
    if action:
        return action

    approved = payload.get("approved")
    if approved is True:
        return _ACTION_ACCEPT

    legacy = _normalize_action(state.get("user_action"))
    if legacy:
        return legacy

    return ""


def _clear_legacy_review_inputs() -> Dict[str, Any]:
    return {"user_action": None, "user_feedback": None}


def _canonical_role(value: Any) -> str:
    raw = getattr(value, "value", value)
    role = _text(raw).lower()
    if role == "drums":
        return "percussion"
    return role


def _clear_review_state() -> Dict[str, Any]:
    return {
        "concept_review": {},
        "blueprint_review": {},
        "pending_user_action": None,
        **_clear_legacy_review_inputs(),
    }


async def generate_concept_node(state: MusicState) -> Dict[str, Any]:
    """Generate SongConcept from user request."""
    logger.info(">>> NODE: Generating Concept")
    agent = ConceptAgent()

    try:
        concept = await agent.generate_concept(_text(state.get("user_request", ""), 1000))
        return {
            "concept": concept,
            "blueprint": None,
            "tracks": {},
            "concept_summary_short": "",
            "concept_summary_long": "",
            "blueprint_summary_short": "",
            "blueprint_summary_long": "",
            "current_phase": "concept_generated",
            "error": None,
            **_clear_review_state(),
        }
    except Exception as exc:
        logger.exception("Concept generation failed")
        return {"error": str(exc), "current_phase": "failed"}


async def summarize_concept_node(state: MusicState) -> Dict[str, Any]:
    """Generate short/long concept summaries and enter review wait state."""
    concept = _as_concept(state.get("concept"))
    if concept is None:
        return {"error": "missing_concept", "current_phase": "failed"}

    summaries = build_concept_summaries(concept)
    return {
        "concept_summary_short": _text(summaries.get("short", ""), 400),
        "concept_summary_long": _text(summaries.get("long", ""), 2400),
        "concept_review": {"approved": None, "action": None, "feedback": None},
        "pending_user_action": "concept_review",
        "current_phase": "concept_review",
        "error": None,
        **_clear_legacy_review_inputs(),
    }


async def concept_review_gate_node(state: MusicState) -> Dict[str, Any]:
    """Route concept review decision."""
    action = _action_from_state(state, "concept_review")
    feedback = _feedback_from_state(state, "concept_review")

    if not action:
        return {
            "pending_user_action": "concept_review",
            "current_phase": "concept_review",
            "error": None,
        }

    if action == _ACTION_ACCEPT:
        return {
            "pending_user_action": None,
            "current_phase": "blueprint_build",
            "concept_review": {"approved": True, "action": _ACTION_ACCEPT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_EDIT:
        return {
            "pending_user_action": None,
            "current_phase": "concept_revise",
            "concept_review": {"approved": False, "action": _ACTION_EDIT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_REGENERATE:
        return {
            "pending_user_action": None,
            "current_phase": "concept_regenerate",
            "concept_review": {"approved": False, "action": _ACTION_REGENERATE, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_ABORT:
        return {
            "pending_user_action": None,
            "current_phase": "aborted",
            "concept_review": {"approved": False, "action": _ACTION_ABORT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    return {
        "pending_user_action": "concept_review",
        "current_phase": "concept_review",
        "error": f"unsupported_concept_action:{action}",
    }


async def revise_concept_node(state: MusicState) -> Dict[str, Any]:
    """Revise concept using user feedback and re-enter summarize/review cycle."""
    logger.info(">>> NODE: Revising Concept")
    base_concept = _as_concept(state.get("concept"))
    feedback = _feedback_from_state(state, "concept_review")
    agent = ConceptAgent()

    try:
        if base_concept is not None and feedback and hasattr(agent, "arevise_concept"):
            concept = await agent.arevise_concept(base_concept, feedback)
        else:
            prompt = _text(state.get("user_request", ""), 1000)
            if feedback:
                prompt = f"{prompt}\nRevision Note: {feedback}"
            concept = await agent.generate_concept(prompt)

        return {
            "concept": concept,
            "blueprint": None,
            "tracks": {},
            "concept_summary_short": "",
            "concept_summary_long": "",
            "blueprint_summary_short": "",
            "blueprint_summary_long": "",
            "current_phase": "concept_generated",
            "error": None,
            **_clear_review_state(),
        }
    except Exception as exc:
        logger.exception("Concept revision failed")
        return {"error": str(exc), "current_phase": "failed"}


async def generate_blueprint_node(state: MusicState) -> Dict[str, Any]:
    """Generate SongBlueprint sections from concept + concept short summary."""
    logger.info(">>> NODE: Generating Blueprint")
    concept = _as_concept(state.get("concept"))
    if concept is None:
        return {"error": "missing_concept", "current_phase": "failed"}

    feedback = _feedback_from_state(state, "blueprint_review")
    concept_hint = _text(state.get("concept_summary_short", ""), 500)
    prompt_parts = [part for part in [concept_hint, feedback] if part]
    user_prompt = "\n".join(prompt_parts)

    agent = DirectorAgent()
    try:
        blueprint = await agent.generate_blueprint(concept, user_prompt=user_prompt)
        return {
            "blueprint": blueprint,
            "tracks": {},
            "blueprint_summary_short": "",
            "blueprint_summary_long": "",
            "current_phase": "blueprint_generated",
            "error": None,
            "pending_user_action": None,
            "blueprint_review": {},
            **_clear_legacy_review_inputs(),
        }
    except Exception as exc:
        logger.exception("Blueprint generation failed")
        return {"error": str(exc), "current_phase": "failed"}


async def summarize_blueprint_node(state: MusicState) -> Dict[str, Any]:
    """Generate short/long blueprint summaries and enter review wait state."""
    blueprint = _as_blueprint(state.get("blueprint"))
    if blueprint is None:
        return {"error": "missing_blueprint", "current_phase": "failed"}

    summaries = build_blueprint_summaries(blueprint)
    return {
        "blueprint_summary_short": _text(summaries.get("short", ""), 500),
        "blueprint_summary_long": _text(summaries.get("long", ""), 3000),
        "blueprint_review": {"approved": None, "action": None, "feedback": None},
        "pending_user_action": "blueprint_review",
        "current_phase": "blueprint_review",
        "error": None,
        **_clear_legacy_review_inputs(),
    }


async def blueprint_review_gate_node(state: MusicState) -> Dict[str, Any]:
    """Route blueprint review decision."""
    action = _action_from_state(state, "blueprint_review")
    feedback = _feedback_from_state(state, "blueprint_review")

    if not action:
        return {
            "pending_user_action": "blueprint_review",
            "current_phase": "blueprint_review",
            "error": None,
        }

    if action == _ACTION_ACCEPT:
        return {
            "pending_user_action": None,
            "current_phase": "production_build",
            "blueprint_review": {"approved": True, "action": _ACTION_ACCEPT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_EDIT:
        return {
            "pending_user_action": None,
            "current_phase": "blueprint_revise",
            "blueprint_review": {"approved": False, "action": _ACTION_EDIT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_REGENERATE:
        return {
            "pending_user_action": None,
            "current_phase": "blueprint_regenerate",
            "blueprint_review": {"approved": False, "action": _ACTION_REGENERATE, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    if action == _ACTION_ABORT:
        return {
            "pending_user_action": None,
            "current_phase": "aborted",
            "blueprint_review": {"approved": False, "action": _ACTION_ABORT, "feedback": feedback or None},
            "error": None,
            **_clear_legacy_review_inputs(),
        }

    return {
        "pending_user_action": "blueprint_review",
        "current_phase": "blueprint_review",
        "error": f"unsupported_blueprint_action:{action}",
    }


async def revise_blueprint_node(state: MusicState) -> Dict[str, Any]:
    """Revise blueprint using feedback and re-enter summarize/review cycle."""
    logger.info(">>> NODE: Revising Blueprint")
    concept = _as_concept(state.get("concept"))
    if concept is None:
        return {"error": "missing_concept", "current_phase": "failed"}

    feedback = _feedback_from_state(state, "blueprint_review")
    concept_short = _text(state.get("concept_summary_short", ""), 300)
    blueprint_short = _text(state.get("blueprint_summary_short", ""), 300)

    prompt_parts = [part for part in [concept_short, blueprint_short] if part]
    if feedback:
        prompt_parts.append(f"Revision Request: {feedback}")
    else:
        prompt_parts.append("Revision Request: refine section pacing and transitions.")
    user_prompt = "\n".join(prompt_parts)

    agent = DirectorAgent()
    try:
        blueprint = await agent.generate_blueprint(concept, user_prompt=user_prompt)
        return {
            "blueprint": blueprint,
            "tracks": {},
            "blueprint_summary_short": "",
            "blueprint_summary_long": "",
            "current_phase": "blueprint_generated",
            "error": None,
            "pending_user_action": None,
            "blueprint_review": {},
            **_clear_legacy_review_inputs(),
        }
    except Exception as exc:
        logger.exception("Blueprint revision failed")
        return {"error": str(exc), "current_phase": "failed"}


async def _run_section_pipeline(
    section_requests: List[Any],
    shared_tracks: Dict[str, Any],
    lock: asyncio.Lock,
) -> Dict[str, Any]:
    """单个 section 内按 compute_layer 顺序执行，section 间并行。"""
    layers = sorted({int(getattr(req, "compute_layer", 0) or 0) for req in section_requests})
    section_updates: Dict[str, Any] = {}

    for layer in layers:
        batch = [r for r in section_requests if int(getattr(r, "compute_layer", 0) or 0) == layer]
        if not batch:
            continue

        # 读取当前已有轨道作为上下文快照
        async with lock:
            context_snapshot = dict(shared_tracks)

        tasks = [run_one_track(req, runtime_context=context_snapshot) for req in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        layer_updates: Dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                logger.warning("Track run failed in layer %s: %s", layer, res)
                continue
            if not res:
                continue

            key = _text(getattr(res, "track_key", ""), 160)
            if not key:
                continue

            role = _canonical_role(getattr(res, "instrument", ""))
            if role:
                try:
                    res.instrument = role
                except Exception:
                    pass

            layer_updates[key] = res

        section_updates.update(layer_updates)
        # 写回共享轨道表，供其他 section 的后续层参考
        async with lock:
            shared_tracks.update(layer_updates)

    return section_updates


async def generate_tracks_node(state: MusicState) -> Dict[str, Any]:
    """Dispatch requests and run role agents with section-level pipelining."""
    logger.info(">>> NODE: Generating Tracks")
    concept = _as_concept(state.get("concept"))
    blueprint = _as_blueprint(state.get("blueprint"))
    if concept is None or blueprint is None:
        return {"error": "missing_concept_or_blueprint", "current_phase": "failed"}

    strictness = max(1, _safe_int(state.get("strictness", 1), 1))
    dispatcher = TrackDispatcher()
    all_requests = dispatcher.dispatch_blueprint(blueprint, concept=concept, strictness=strictness)
    logger.info("Dispatched %s track requests.", len(all_requests))

    # 按 section_index 分组
    from collections import defaultdict
    section_groups: Dict[int, list] = defaultdict(list)
    for req in all_requests:
        idx = int(getattr(req, "section_index", 0) or 0)
        section_groups[idx].append(req)

    shared_tracks: Dict[str, Any] = dict(state.get("tracks", {}) or {})
    lock = asyncio.Lock()

    # 所有 section 并行启动，各自内部按 layer 顺序推进
    section_tasks = [
        _run_section_pipeline(reqs, shared_tracks, lock)
        for _, reqs in sorted(section_groups.items())
    ]
    logger.info("Running %s sections in pipeline mode...", len(section_tasks))
    section_results = await asyncio.gather(*section_tasks, return_exceptions=True)

    generated_updates: Dict[str, Any] = {}
    for res in section_results:
        if isinstance(res, Exception):
            logger.warning("Section pipeline failed: %s", res)
            continue
        if isinstance(res, dict):
            generated_updates.update(res)

    # Build retry request map keyed by track_key
    retry_requests: Dict[str, Any] = {}
    for req in all_requests:
        key = _text(getattr(req, "track_key", ""), 160)
        if key:
            retry_requests[key] = req.model_dump()

    return {
        "tracks": generated_updates,
        "track_retry_requests": retry_requests,
        "track_retry_count": 0,
        "track_quality_issues": {},
        "pending_user_action": None,
        "current_phase": "quality_check",
        "error": None,
        **_clear_legacy_review_inputs(),
    }


async def quality_gate_node(state: MusicState) -> Dict[str, Any]:
    """Validate track quality; route to retry, gap-fill, or done."""
    logger.info(">>> NODE: Quality Gate (retry_count=%s)", state.get("track_retry_count", 0))
    tracks = state.get("tracks") or {}
    request_map = state.get("track_retry_requests") or {}
    retry_count = int(state.get("track_retry_count", 0) or 0)

    if not tracks or not request_map:
        logger.info("Quality gate: no tracks or request_map, passing through.")
        return {"current_phase": "done"}

    results = validate_all_tracks(tracks, request_map)
    failed = {k: r for k, r in results.items() if not r.passed}

    if not failed:
        logger.info("Quality gate: all %s tracks passed.", len(results))
        return {"current_phase": "done", "track_quality_issues": {}}

    logger.warning("Quality gate: %s/%s tracks failed.", len(failed), len(results))

    if retry_count < QG_MAX_RETRIES:
        # Build issue descriptions for retry prompt
        issues: Dict[str, str] = {}
        for key, result in failed.items():
            req_data = request_map.get(key) or {}
            bar_ticks = int(req_data.get("bar_ticks", 1920)) if isinstance(req_data, dict) else int(getattr(req_data, "bar_ticks", 1920))
            issues[key] = format_issue_feedback(result, bar_ticks)
        return {
            "track_quality_issues": issues,
            "current_phase": "track_retry",
        }

    # Retries exhausted — programmatic gap fill
    logger.info("Quality gate: retries exhausted, applying programmatic gap fill.")
    patched_tracks: Dict[str, Any] = {}
    for key, result in failed.items():
        track = tracks.get(key)
        if track is None:
            continue
        req_data = request_map.get(key) or {}
        if isinstance(req_data, dict):
            start_tick = int(req_data.get("start_tick", 0))
            end_tick = int(req_data.get("end_tick", 0))
            bar_ticks = int(req_data.get("bar_ticks", 1920))
        else:
            start_tick = int(getattr(req_data, "start_tick", 0))
            end_tick = int(getattr(req_data, "end_tick", 0))
            bar_ticks = int(getattr(req_data, "bar_ticks", 1920))

        if end_tick <= start_tick:
            continue

        notes_raw = getattr(track, "notes", None)
        if notes_raw is None and isinstance(track, dict):
            notes_raw = track.get("notes", [])

        filled = fill_gaps_by_pattern_repeat(notes_raw, start_tick, end_tick, bar_ticks)

        # Reconstruct track with filled notes
        if hasattr(track, "model_copy"):
            patched_tracks[key] = track.model_copy(update={"notes": filled})
        elif isinstance(track, dict):
            patched_tracks[key] = {**track, "notes": [n.model_dump() for n in filled]}
        else:
            patched_tracks[key] = track

    return {
        "tracks": patched_tracks,
        "track_quality_issues": {},
        "current_phase": "done",
    }


async def retry_failed_tracks_node(state: MusicState) -> Dict[str, Any]:
    """Re-generate only the tracks that failed quality checks."""
    logger.info(">>> NODE: Retry Failed Tracks")
    issues = state.get("track_quality_issues") or {}
    request_map = state.get("track_retry_requests") or {}
    retry_count = int(state.get("track_retry_count", 0) or 0)
    existing_tracks = state.get("tracks") or {}

    from schema.request import TrackGenRequest as _TGR

    retry_updates: Dict[str, Any] = {}
    tasks = []
    task_keys: List[str] = []

    for key, issue_text in issues.items():
        req_data = request_map.get(key)
        if not req_data:
            logger.warning("Retry: no request data for %s, skipping.", key)
            continue
        try:
            req = _TGR.model_validate(req_data)
        except Exception as exc:
            logger.warning("Retry: failed to restore request for %s: %s", key, exc)
            continue

        # Append retry feedback to context_summary
        existing_ctx = str(getattr(req, "context_summary", "") or "")
        req.context_summary = f"{existing_ctx}\n{issue_text}".strip()

        tasks.append(run_one_track(req, runtime_context=existing_tracks))
        task_keys.append(key)

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, res in zip(task_keys, results):
            if isinstance(res, Exception):
                logger.warning("Retry run failed for %s: %s", key, res)
                continue
            if res is None:
                continue
            tk = _text(getattr(res, "track_key", ""), 160)
            if not tk:
                continue

            role = _canonical_role(getattr(res, "instrument", ""))
            if role:
                try:
                    res.instrument = role
                except Exception:
                    pass

            retry_updates[tk] = res

    return {
        "tracks": retry_updates,
        "track_retry_count": retry_count + 1,
        "current_phase": "quality_check",
    }


__all__ = [
    "generate_concept_node",
    "summarize_concept_node",
    "concept_review_gate_node",
    "revise_concept_node",
    "generate_blueprint_node",
    "summarize_blueprint_node",
    "blueprint_review_gate_node",
    "revise_blueprint_node",
    "generate_tracks_node",
    "quality_gate_node",
    "retry_failed_tracks_node",
]

