"""MCP service aligned with graph review gates and structured status APIs."""

import asyncio
import json
import logging
import re
import sys
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from fastmcp import Context, FastMCP
except Exception:  # pragma: no cover - local fallback when fastmcp is unavailable
    class Context:  # type: ignore
        session_id: Optional[str] = None

    class FastMCP:  # type: ignore
        def __init__(self, name: str):
            self.name = name

        def tool(self, fn=None):
            if fn is None:
                return lambda inner: inner
            return fn

        def resource(self, *args, **kwargs):
            def _decorator(inner):
                return inner

            return _decorator

        def run(self):
            raise RuntimeError("fastmcp is not installed in this environment.")

from graph.state import MusicState
from graph.workflow import music_app
from midi_renderer import render_tracks_to_midi
from schema.request import GeneratedTrack
from track_builder import TrackOut, bass_to_track, drums_to_track

mcp = FastMCP("MusicGraph Agent")
logger = logging.getLogger(__name__)

_ACTION_ACCEPT = "accept"
_ACTION_EDIT = "edit"
_ACTION_REGENERATE = "regenerate"
_ACTION_ABORT = "abort"

_REVIEW_ACTIONS = {_ACTION_ACCEPT, _ACTION_EDIT, _ACTION_REGENERATE, _ACTION_ABORT}
_LOCAL_SESSION_ID = f"local-{uuid.uuid4().hex}"


@dataclass
class MusicSession:
    last_run_id: Optional[str] = None
    last_section_index: Optional[int] = None


_SESSIONS: Dict[str, MusicSession] = {}


def _ensure_app() -> None:
    if music_app is None:
        raise RuntimeError("music_app is not available. Ensure langgraph is installed.")


def _dump_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return _dump_value(value.model_dump())
        except Exception:
            return value
    if isinstance(value, dict):
        return {k: _dump_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dump_value(v) for v in value]
    return value


def _session_id(ctx: Optional[Context]) -> str:
    raw = str(getattr(ctx, "session_id", "") or "").strip()
    if raw:
        return raw
    return _LOCAL_SESSION_ID


def _get_session(ctx: Optional[Context]) -> MusicSession:
    sid = _session_id(ctx)
    if sid not in _SESSIONS:
        _SESSIONS[sid] = MusicSession()
    return _SESSIONS[sid]


def _resolve_run_id(run_id: Optional[str], session: MusicSession) -> str:
    rid = str(run_id or "").strip()
    if rid:
        session.last_run_id = rid
        return rid
    if session.last_run_id:
        return session.last_run_id
    raise ValueError("No run_id available. Call start_new_song or generate_full_song first.")


def _config_for_run(run_id: str) -> Dict[str, Dict[str, str]]:
    return {"configurable": {"thread_id": str(run_id)}}


async def _get_state(run_id: str) -> MusicState:
    """异步获取当前 Graph 的快照状态"""
    _ensure_app()
    # 使用 aget_state 替代 get_state
    snapshot = await music_app.aget_state(_config_for_run(run_id))
    values = getattr(snapshot, "values", None)
    if not isinstance(values, dict):
        return {}
    return values

async def _update_state(run_id: str, values: Dict[str, Any]):
    """异步更新状态"""
    _ensure_app()
    # 使用 aupdate_state 替代 update_state
    await music_app.aupdate_state(_config_for_run(run_id), values)


# ---------------------------------------------------------------------------
# Background graph execution (fire-and-forget, survives MCP cancellation)
# ---------------------------------------------------------------------------

_BG_TASKS: Dict[str, asyncio.Task] = {}


async def _bg_invoke(run_id: str) -> None:
    """Run ainvoke in the background; log errors but never propagate."""
    try:
        await music_app.ainvoke(None, _config_for_run(run_id))
    except asyncio.CancelledError:
        logger.warning("Background invoke for run %s was cancelled.", run_id)
    except Exception as exc:
        logger.exception("Background invoke for run %s failed: %s", run_id, exc)
        # Best-effort: mark state as failed so polling can see it
        try:
            await _update_state(run_id, {"error": str(exc), "current_phase": "failed"})
        except Exception:
            pass
    finally:
        _BG_TASKS.pop(run_id, None)


def _start_bg_invoke(run_id: str) -> None:
    """Fire-and-forget graph execution that won't be cancelled by MCP timeout."""
    old = _BG_TASKS.pop(run_id, None)
    if old is not None and not old.done():
        old.cancel()
    _BG_TASKS[run_id] = asyncio.create_task(_bg_invoke(run_id))


def _phase(state: MusicState) -> str:
    return str(state.get("current_phase", "")).strip() or "unknown"


def _pending(state: MusicState) -> Optional[str]:
    raw = state.get("pending_user_action")
    text = str(raw or "").strip()
    return text or None


_PENDING_ACTION_INSTRUCTIONS = {
    "concept_review": (
        "STOP: You MUST present the concept to the user and WAIT for their explicit decision. "
        "Ask the user whether to: accept (proceed to blueprint), edit (revise with feedback), "
        "regenerate (start over), or abort. Do NOT call review_concept until the user responds."
    ),
    "blueprint_review": (
        "STOP: You MUST present the blueprint to the user and WAIT for their explicit decision. "
        "Ask the user whether to: accept (proceed to track generation), edit (revise with feedback), "
        "regenerate (start over), or abort. Do NOT call review_blueprint until the user responds."
    ),
}


def _normalize_status(run_id: str, state: MusicState, *, message: str = "") -> Dict[str, Any]:
    phase = _phase(state)
    pending = _pending(state)
    error = _dump_value(state.get("error"))
    tracks = state.get("tracks") or {}

    # 当存在待用户操作时，强制附加交互指令
    final_message = str(message or "")
    if pending and pending in _PENDING_ACTION_INSTRUCTIONS:
        final_message = _PENDING_ACTION_INSTRUCTIONS[pending]

    out: Dict[str, Any] = {
        "ok": error is None,
        "run_id": str(run_id),
        "phase": phase,
        "pending_user_action": pending,
        "ready": phase == "done",
        "error": error,
        "track_count": len(tracks),
        "message": final_message,
    }

    # Map internal phases to a non-ready producing status
    if phase in ("quality_check", "track_retry", "production_build"):
        out["ready"] = False
        out["phase"] = "producing"
    if "concept_summary_short" in state:
        out["concept_summary_short"] = _dump_value(state.get("concept_summary_short"))
    if "blueprint_summary_short" in state:
        out["blueprint_summary_short"] = _dump_value(state.get("blueprint_summary_short"))
    return out


def _normalize_action(action: str) -> str:
    text = str(action or "").strip().lower()
    if text in _REVIEW_ACTIONS:
        return text
    if text in ("approve", "approved", "ok", "yes"):
        return _ACTION_ACCEPT
    if text in ("revise", "update"):
        return _ACTION_EDIT
    if text in ("redo", "regen", "rerun"):
        return _ACTION_REGENERATE
    if text in ("stop", "cancel"):
        return _ACTION_ABORT
    return ""


def _review_update_payload(target: str, action: str, feedback: str) -> Dict[str, Any]:
    approved = action == _ACTION_ACCEPT
    payload = {
        target: {
            "approved": approved,
            "action": action,
            "feedback": feedback or None,
        },
        # Legacy compatibility fields are still consumed by graph.nodes.
        "user_action": action,
        "user_feedback": feedback or None,
    }
    return payload


def _normalize_role(value: Any) -> str:
    role = str(getattr(value, "value", value) or "").strip().lower()
    if role == "drums":
        return "percussion"
    return role


def _section_index_from_track_key(track_key: str) -> Optional[int]:
    match = re.match(r"^s(\d+)_", str(track_key or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _to_generated_track(raw: Any, fallback_key: str) -> Optional[GeneratedTrack]:
    if isinstance(raw, GeneratedTrack):
        return raw
    if isinstance(raw, dict):
        data = dict(raw)
        if not data.get("track_key"):
            data["track_key"] = str(fallback_key or "unknown")
        if not data.get("section_name"):
            data["section_name"] = "Section"
        if not data.get("instrument"):
            data["instrument"] = "melody"
        try:
            track = GeneratedTrack.model_validate(data)
        except Exception:
            return None
        track.instrument = _normalize_role(track.instrument)
        return track
    return None


def _channel_for_role(role: str) -> int:
    if role == "percussion":
        return 9
    if role == "bass":
        return 1
    if role == "harmony":
        return 2
    if role == "melody":
        return 3
    if role == "fx":
        return 4
    return 0


_GM_PROGRAM = {
    "bass": 33,        # Electric Bass (Finger)
    "melody": 0,       # Acoustic Grand Piano
    "harmony": 48,     # String Ensemble
    "fx": 122,         # Seashore
    "percussion": None, # drums 不需要 program change
}


def _to_track_out(track: GeneratedTrack) -> Optional[TrackOut]:
    role = _normalize_role(track.instrument)
    raw = track.raw_output

    if raw is not None:
        if role == "percussion":
            try:
                return drums_to_track(raw)
            except Exception:
                pass
        if role == "bass":
            try:
                return bass_to_track(raw)
            except Exception:
                pass

    notes = list(track.notes or [])
    if not notes:
        return None

    return TrackOut(
        name=f"{track.section_name}_{role or 'track'}",
        instrument=role or "unknown",
        channel=_channel_for_role(role),
        notes=notes,
        program=_GM_PROGRAM.get(role),
    )


def _collect_tracks_for_export(
    state: MusicState,
    *,
    section_index: Optional[int] = None,
) -> List[GeneratedTrack]:
    tracks = state.get("tracks") or {}
    out: List[GeneratedTrack] = []
    for key, value in tracks.items():
        idx = _section_index_from_track_key(str(key))
        if section_index is not None and idx is not None and idx != int(section_index):
            continue
        track = _to_generated_track(value, fallback_key=str(key))
        if track is None:
            continue
        out.append(track)
    return out


async def _start_new_song_impl(
    user_prompt: str,
    strictness: int = 1,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Core implementation shared by start_new_song and generate_full_song."""
    _ensure_app()
    session = _get_session(ctx)
    run_id = uuid.uuid4().hex[:12]

    initial_state: Dict[str, Any] = {
        "run_id": run_id,
        "session_id": _session_id(ctx),
        "user_request": str(user_prompt or ""),
        "strictness": int(strictness),
        "tracks": {},
        "current_phase": "init",
        "concept_review": {},
        "blueprint_review": {},
        "pending_user_action": None,
        "user_action": None,
        "user_feedback": None,
    }

    await music_app.ainvoke(initial_state, _config_for_run(run_id))
    session.last_run_id = run_id
    state = await _get_state(run_id)
    status = _normalize_status(run_id, state, message="run started")
    concept = _dump_value(state.get("concept"))
    if concept is not None:
        status["concept"] = concept
    return status


@mcp.tool
async def start_new_song(
    user_prompt: str,
    strictness: int = 1,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Start a new song run. Returns a concept for user review.

    IMPORTANT: After calling this tool, you MUST present the generated concept
    to the user and WAIT for their explicit decision before calling review_concept.
    Do NOT automatically accept or proceed. The user must choose: accept / edit / regenerate / abort.
    """
    return await _start_new_song_impl(user_prompt=user_prompt, strictness=int(strictness), ctx=ctx)


@mcp.tool
async def generate_full_song(
    user_request: str,
    strictness: int = 1,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Start a new song run (alias for start_new_song). Returns a concept for user review.

    IMPORTANT: After calling this tool, you MUST present the generated concept
    to the user and WAIT for their explicit decision before calling review_concept.
    Do NOT automatically accept or proceed. The user must choose: accept / edit / regenerate / abort.
    """
    _ = task_progress
    return await _start_new_song_impl(user_prompt=user_request, strictness=int(strictness), ctx=ctx)


@mcp.tool
async def review_concept(
    action: Literal["accept", "edit", "regenerate", "abort"],
    feedback: str = "",
    run_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Submit the user's concept review decision and resume graph execution.

    IMPORTANT: Only call this tool AFTER the user has explicitly chosen an action.
    Never call this automatically. The action parameter must reflect the user's actual choice.
    If action is 'edit' or 'regenerate', include the user's feedback.
    """
    _ensure_app()
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    state = await _get_state(rid)
    status = _normalize_status(rid, state)

    if status.get("pending_user_action") != "concept_review":
        status["ok"] = False
        status["message"] = f"phase_mismatch: pending={status.get('pending_user_action')!r}, phase={status.get('phase')!r}"
        return status

    normalized = _normalize_action(action)
    if not normalized:
        return {
            **status,
            "ok": False,
            "message": f"invalid_action:{action}",
        }
    payload = _review_update_payload("concept_review", normalized, str(feedback or "").strip())
    config = _config_for_run(rid)
    await music_app.aupdate_state(config, payload)

    # For non-accept actions the graph pauses quickly (at next review gate),
    # so synchronous await is fine.  For accept the downstream nodes
    # (blueprint generation) are fast enough to await inline.
    await music_app.ainvoke(None, config)

    next_state = await _get_state(rid)
    session.last_run_id = rid
    return _normalize_status(rid, next_state, message="concept review applied")


@mcp.tool
async def review_blueprint(
    action: Literal["accept", "edit", "regenerate", "abort"],
    feedback: str = "",
    run_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Submit the user's blueprint review decision and resume graph execution.

    IMPORTANT: Only call this tool AFTER the user has explicitly chosen an action.
    Never call this automatically. The action parameter must reflect the user's actual choice.
    If action is 'edit' or 'regenerate', include the user's feedback.
    """
    _ensure_app()
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    state = await _get_state(rid)
    status = _normalize_status(rid, state)

    if status.get("pending_user_action") != "blueprint_review":
        status["ok"] = False
        status["message"] = f"phase_mismatch: pending={status.get('pending_user_action')!r}, phase={status.get('phase')!r}"
        return status

    normalized = _normalize_action(action)
    if not normalized:
        return {
            **status,
            "ok": False,
            "message": f"invalid_action:{action}",
        }
    payload = _review_update_payload("blueprint_review", normalized, str(feedback or "").strip())
    config = _config_for_run(rid)
    await music_app.aupdate_state(config, payload)

    if normalized == _ACTION_ACCEPT:
        # Track generation (compose_music + quality_gate + retries) is long-running.
        # Run graph in background so MCP timeout won't kill it.
        _start_bg_invoke(rid)
        session.last_run_id = rid
        state_now = await _get_state(rid)
        return _normalize_status(rid, state_now, message="blueprint accepted, track generation started in background. Poll get_song_status to check progress.")
    else:
        # edit / regenerate / abort finish quickly (next interrupt is nearby)
        await music_app.ainvoke(None, config)
        next_state = await _get_state(rid)
        session.last_run_id = rid
        return _normalize_status(rid, next_state, message="blueprint review applied")


@mcp.tool
async def get_song_status(
    run_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Read normalized status for the current run."""
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    state = await _get_state(rid)
    session.last_run_id = rid
    return _normalize_status(rid, state, message="status")


@mcp.tool
async def get_song_result(
    run_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Read full run payload (concept/blueprint/tracks) when available."""
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    state = await _get_state(rid)
    status = _normalize_status(rid, state, message="result")
    status["concept"] = _dump_value(state.get("concept"))
    status["blueprint"] = _dump_value(state.get("blueprint"))
    status["tracks"] = _dump_value(state.get("tracks") or {})
    session.last_run_id = rid
    return status


def _build_midi_filename(concept: Dict[str, Any]) -> str:
    """从 concept 中提取风格描述作为文件名，加上日期时间后缀。"""
    raw_name = str(concept.get("style_description") or concept.get("mood") or "untitled").strip()
    # 只保留字母、数字、中文、空格、连字符
    sanitized = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", raw_name)
    # 空格/连续空白替换为下划线
    sanitized = re.sub(r"\s+", "_", sanitized.strip())
    # 限制长度
    if len(sanitized) > 60:
        sanitized = sanitized[:60]
    if not sanitized:
        sanitized = "untitled"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{sanitized}_{timestamp}.mid"


@mcp.tool
async def export_midi(
    filename: str = "output.mid",
    section_index: Optional[int] = None,
    run_id: Optional[str] = None,
    ctx: Context = None,
) -> Dict[str, Any]:
    """Export generated tracks to a MIDI file."""
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    state = await _get_state(rid)
    phase = _phase(state)
    if phase != "done":
        return {
            "ok": False,
            "run_id": rid,
            "phase": phase,
            "ready": False,
            "error": "result_not_ready",
            "message": "Run is not finished yet.",
        }

    tracks = _collect_tracks_for_export(state, section_index=section_index)
    midi_tracks: List[TrackOut] = []
    for track in tracks:
        item = _to_track_out(track)
        if item is not None:
            midi_tracks.append(item)

    if not midi_tracks:
        return {
            "ok": False,
            "run_id": rid,
            "phase": phase,
            "ready": True,
            "error": "no_tracks_for_export",
            "message": "No exportable tracks available.",
        }

    concept = _dump_value(state.get("concept") or {})
    bpm = int((concept.get("bpm") or 120))

    blueprint = _dump_value(state.get("blueprint") or {})
    target_duration = float(blueprint.get("user_confirmed_duration_sec") or 0)

    midi_dir = ROOT_DIR / "data" / "midi_all"
    midi_dir.mkdir(parents=True, exist_ok=True)
    out_path = midi_dir / _build_midi_filename(concept)

    render_tracks_to_midi(
        tracks=midi_tracks,
        bpm=bpm,
        out_path=str(out_path),
        strictness=1,
        target_duration_sec=target_duration if target_duration > 0 else None,
    )

    if section_index is not None:
        session.last_section_index = int(section_index)
    session.last_run_id = rid
    return {
        "ok": True,
        "run_id": rid,
        "phase": phase,
        "ready": True,
        "out_path": str(out_path),
        "bpm": bpm,
        "track_count": len(midi_tracks),
        "section_index": section_index,
    }


@mcp.resource("music://state/current")
async def get_state_resource(ctx: Context = None) -> str:
    """View current normalized state for the active session."""
    session = _get_session(ctx)
    if not session.last_run_id:
        return json.dumps({"message": "No run in this session."}, ensure_ascii=False, indent=2)

    rid = session.last_run_id
    state = await _get_state(rid)
    out = _normalize_status(rid, state, message="resource_state")
    out["concept"] = _dump_value(state.get("concept"))
    out["blueprint"] = _dump_value(state.get("blueprint"))
    out["track_keys"] = list((_dump_value(state.get("tracks") or {}) or {}).keys())
    return json.dumps(out, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
