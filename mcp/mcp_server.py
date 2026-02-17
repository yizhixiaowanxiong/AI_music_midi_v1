"""MCP 服务入口：提供异步编排工具与资源查询接口。"""

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import hashlib
from typing import Any, Dict, List, Optional
import uuid

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from fastmcp import Context, FastMCP
    from fastmcp.server.dependencies import get_http_headers
except Exception:  # pragma: no cover - local fallback when fastmcp is unavailable
    class Context:  # type: ignore
        session_id: Optional[str] = None

    def get_http_headers(include_all: bool = True):  # type: ignore
        return {}

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

from blueprint_report import format_report
from graph.orchestrator import get_orchestrator
from midi_renderer import render_tracks_to_midi
from observability.metrics_server import maybe_start_metrics_server
from schema.base import AgentRoutingRole, NoteEvent
from track_builder import TrackOut, bass_to_track, drums_to_track


mcp = FastMCP(name="AiAgentMusic MCP")
_ORCH = get_orchestrator()


@dataclass
class MusicSession:
    last_run_id: Optional[str] = None
    last_section_index: Optional[int] = None


_SESSIONS: Dict[str, MusicSession] = {}
_CTX_FALLBACK_SESSIONS: Dict[int, str] = {}
_LOCAL_SESSION_ID = f"local-{uuid.uuid4().hex}"
_REVIEW_TASKS: Dict[str, asyncio.Task] = {}


def _resolve_session_id(ctx: Optional[Context]) -> str:
    headers = get_http_headers(include_all=True) or {}
    for key in ("session-id", "session_id", "mcp-session-id", "x-session-id"):
        value = headers.get(key)
        if value:
            return value

    # 当网关未显式透传 session-id 时，尽量使用请求指纹做弱稳定隔离，避免所有请求共享同一默认会话。
    fp_parts = []
    for key in ("x-forwarded-for", "x-real-ip", "user-agent"):
        value = str(headers.get(key) or "").strip()
        if value:
            fp_parts.append(f"{key}={value}")
    if fp_parts:
        digest = hashlib.sha1("|".join(fp_parts).encode("utf-8")).hexdigest()[:16]
        return f"anon-{digest}"

    if ctx and getattr(ctx, "session_id", None):
        return ctx.session_id

    # 仍无可用标识时：按 Context 对象粒度分配回退会话；本地无 ctx 时使用进程级本地会话。
    if ctx is not None:
        key = id(ctx)
        if key not in _CTX_FALLBACK_SESSIONS:
            _CTX_FALLBACK_SESSIONS[key] = f"ctx-{uuid.uuid4().hex}"
        return _CTX_FALLBACK_SESSIONS[key]
    return _LOCAL_SESSION_ID


def _get_session(ctx: Optional[Context]) -> MusicSession:
    sid = _resolve_session_id(ctx)
    if sid not in _SESSIONS:
        _SESSIONS[sid] = MusicSession()
    return _SESSIONS[sid]


def _resolve_run_id(run_id: Optional[str], session: MusicSession) -> str:
    # 优先使用显式 run_id；未传时回落到会话内最近一次 run。
    rid = str(run_id or "").strip()
    if rid:
        session.last_run_id = rid
        return rid
    if session.last_run_id:
        return session.last_run_id
    raise ValueError("No run_id available. Call generate_full_song first.")


def _is_review_task_running(run_id: str) -> bool:
    task = _REVIEW_TASKS.get(run_id)
    if task is None:
        return False
    if task.done():
        _REVIEW_TASKS.pop(run_id, None)
        return False
    return True


def _start_review_task(
    run_id: str,
    approved: bool,
    feedback: Optional[str],
    user_confirmed_duration_sec: Optional[float],
    concept_updates: Optional[Dict[str, Any]],
) -> bool:
    if _is_review_task_running(run_id):
        return False

    async def _runner() -> None:
        try:
            await _ORCH.submit_concept_review(
                run_id=run_id,
                approved=bool(approved),
                feedback=feedback,
                user_confirmed_duration_sec=user_confirmed_duration_sec,
                concept_updates=concept_updates,
            )
        finally:
            _REVIEW_TASKS.pop(run_id, None)

    task = asyncio.create_task(_runner(), name=f"review-{run_id[:8]}")

    def _consume_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except BaseException:
            # Errors are persisted into orchestrator state; avoid unhandled-task noise.
            pass

    task.add_done_callback(_consume_result)
    _REVIEW_TASKS[run_id] = task
    return True


def _dump_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _dump_value(value.model_dump())
    if isinstance(value, dict):
        return {k: _dump_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dump_value(v) for v in value]
    return value


async def _require_ready_result(run_id: str) -> Dict[str, Any]:
    # 需要最终结果的工具统一做 ready 校验。
    result = await _ORCH.get_run_result(run_id)
    if not result.get("ready"):
        raise ValueError(
            f"result_not_ready: run={run_id} phase={result.get('phase')!r}"
        )
    return result


def _section_key_from_result(result: Dict[str, Any], section_index: int) -> str:
    blueprint = result.get("blueprint") or {}
    sections = blueprint.get("sections") or []
    if section_index < 0 or section_index >= len(sections):
        raise ValueError("section_index out of range.")
    section = sections[section_index]
    section_name = str(section.get("name") or "")
    tracks = result.get("tracks") or {}

    # 首选运行时索引（与 graph.nodes._section_key 保持一致）。
    runtime_key = f"{section_index}:{section_name}"
    if runtime_key in tracks:
        return runtime_key

    # 兼容旧数据：若 tracks 仍按 blueprint.section.index 编码，则回退该 key。
    declared_idx = section.get("index")
    if isinstance(declared_idx, int):
        legacy_key = f"{declared_idx}:{section_name}"
        if legacy_key in tracks:
            return legacy_key

    # 最后尝试按 section 名称唯一匹配。
    suffix = f":{section_name}"
    matches = [k for k in tracks.keys() if str(k).endswith(suffix)]
    if len(matches) == 1:
        return str(matches[0])
    return runtime_key


def _normalize_instrument(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    text = str(value or "")
    return text.lower()


def _select_track_payload(
    result: Dict[str, Any],
    section_index: int,
    role: AgentRoutingRole,
) -> Dict[str, Any]:
    section_key = _section_key_from_result(result, section_index)
    tracks = (result.get("tracks") or {}).get(section_key, [])
    target = role.value
    for track in tracks:
        if _normalize_instrument(track.get("instrument")) == target:
            return track
    raise ValueError(f"No {target} track found for section_index={section_index}.")


def _track_payload_to_midi_track(track: Dict[str, Any]) -> Optional[TrackOut]:
    instrument = _normalize_instrument(track.get("instrument"))
    raw = track.get("raw_output")

    if raw is not None:
        if instrument in ("percussion", "drums"):
            return drums_to_track(raw)
        if instrument == "bass":
            return bass_to_track(raw)

    if instrument not in ("percussion", "drums", "bass"):
        return None

    notes = [NoteEvent.model_validate(n) for n in (track.get("notes") or [])]
    channel = 9 if instrument in ("percussion", "drums") else 1
    return TrackOut(
        name=f"{track.get('section_name', 'section')}_{instrument}",
        instrument="drums" if channel == 9 else "bass",
        channel=channel,
        notes=notes,
    )


@mcp.tool
async def generate_full_song(
    user_request: str,
    strictness: int = 1,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    # 解析会话上下文并启动新的 run。
    session_id = _resolve_session_id(ctx)
    session = _get_session(ctx)
    status = await _ORCH.start_run(
        user_request=user_request,
        strictness=int(strictness),
        session_id=session_id,
    )
    session.last_run_id = status["run_id"]
    return status


@mcp.tool
async def review_concept(
    approved: bool,
    feedback: Optional[str] = None,
    user_confirmed_duration_sec: Optional[float] = None,
    concept_updates: Optional[Dict[str, Any]] = None,
    background: bool = True,
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    # 在会话范围内提交 concept 审核并推进流程。
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    if not background:
        status = await _ORCH.submit_concept_review(
            run_id=rid,
            approved=bool(approved),
            feedback=feedback,
            user_confirmed_duration_sec=user_confirmed_duration_sec,
            concept_updates=concept_updates,
        )
        status["review_task_running"] = _is_review_task_running(rid)
        session.last_run_id = rid
        return status

    feedback_text = str(feedback or "").strip()
    if concept_updates is not None and not isinstance(concept_updates, dict):
        raise ValueError("concept_updates must be an object.")
    if not approved and not feedback_text and not concept_updates:
        raise ValueError("feedback is required when approved=False.")
    if user_confirmed_duration_sec is not None and float(user_confirmed_duration_sec) <= 0:
        raise ValueError("user_confirmed_duration_sec must be > 0.")

    status = await _ORCH.get_run_status(rid)
    phase = str(status.get("phase") or "")

    if phase in ("done", "failed"):
        status["review_task_running"] = _is_review_task_running(rid)
        session.last_run_id = rid
        return status

    if phase != "waiting_review":
        status["accepted"] = False
        status["review_task_running"] = _is_review_task_running(rid)
        status["message"] = f"run is busy in phase={phase!r}, skip duplicate review submit."
        session.last_run_id = rid
        return status

    accepted = _start_review_task(
        run_id=rid,
        approved=bool(approved),
        feedback=feedback_text or None,
        user_confirmed_duration_sec=user_confirmed_duration_sec,
        concept_updates=concept_updates,
    )
    status["accepted"] = bool(accepted)
    status["review_task_running"] = _is_review_task_running(rid)
    if accepted:
        status["phase"] = "blueprint_build" if approved else "concept_draft"
        status["ready"] = False
        status["message"] = "review accepted; generation is running in background."
    else:
        status["message"] = "review task already running for this run_id."
    session.last_run_id = rid
    return status


@mcp.tool
async def get_song_status(
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    # 轻量状态查询，适合轮询。
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    status = await _ORCH.get_run_status(rid)
    status["review_task_running"] = _is_review_task_running(rid)
    return status


@mcp.tool
async def get_song_result(
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    # 最终结果查询；未完成时返回 ready=False。
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    result = await _ORCH.get_run_result(rid)
    result["review_task_running"] = _is_review_task_running(rid)
    return result


@mcp.tool
async def enrich_section_details(
    section_index: int,
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    result = await _require_ready_result(rid)
    blueprint = result.get("blueprint") or {}
    sections = blueprint.get("sections") or []
    if section_index < 0 or section_index >= len(sections):
        raise ValueError("section_index out of range.")
    session.last_section_index = section_index
    return sections[section_index]


@mcp.tool
async def generate_drums(
    section_index: int,
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    result = await _require_ready_result(rid)
    track = _select_track_payload(result, section_index, AgentRoutingRole.PERCUSSION)
    session.last_section_index = section_index
    return dict(track)


@mcp.tool
async def generate_bass(
    section_index: int,
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    result = await _require_ready_result(rid)
    track = _select_track_payload(result, section_index, AgentRoutingRole.BASS)
    session.last_section_index = section_index
    return dict(track)


@mcp.tool
async def export_midi(
    section_index: Optional[int] = None,
    out_filename: Optional[str] = None,
    run_id: Optional[str] = None,
    task_progress: Optional[str] = None,
    ctx: Context = None,
) -> dict:
    session = _get_session(ctx)
    rid = _resolve_run_id(run_id, session)
    result = await _require_ready_result(rid)
    blueprint = result.get("blueprint") or {}
    sections = blueprint.get("sections") or []

    if section_index is None:
        section_index = session.last_section_index if session.last_section_index is not None else 0
    if section_index < 0 or section_index >= len(sections):
        raise ValueError("section_index out of range.")

    section_key = _section_key_from_result(result, section_index)
    tracks = (result.get("tracks") or {}).get(section_key, [])
    midi_tracks: List[TrackOut] = []
    for track in tracks:
        item = _track_payload_to_midi_track(track)
        if item is not None:
            midi_tracks.append(item)

    if not midi_tracks:
        raise ValueError("No drums/bass tracks available for MIDI export in this section.")

    out_dir = Path("data/midi_all")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not out_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_filename = f"mcp_section_{section_index}_{ts}.mid"
    out_path = str(out_dir / out_filename)

    bpm = int(((blueprint.get("concept") or {}).get("bpm")) or 120)
    render_tracks_to_midi(
        tracks=midi_tracks,
        bpm=bpm,
        out_path=out_path,
        strictness=1,
    )
    session.last_section_index = section_index
    return {
        "run_id": rid,
        "out_path": out_path,
        "tracks": [t.instrument for t in midi_tracks],
        "bpm": bpm,
        "section_index": int(section_index),
    }


@mcp.resource("music://current/concept")
async def current_concept(ctx: Context = None) -> str:
    session = _get_session(ctx)
    if not session.last_run_id:
        return "No concept available in this session."
    status = await _ORCH.get_run_status(session.last_run_id)
    concept = status.get("concept")
    if concept:
        return json.dumps(concept, indent=2, ensure_ascii=False)
    return "No concept available in this run."


@mcp.resource("music://current/blueprint")
async def current_blueprint(ctx: Context = None) -> str:
    session = _get_session(ctx)
    if not session.last_run_id:
        return "No blueprint available in this session."
    raw_state = await _ORCH.get_raw_state(session.last_run_id)
    blueprint = raw_state.get("blueprint")
    if blueprint:
        return format_report(_dump_value(blueprint))
    return (
        "Blueprint is not available yet.\n"
        "Call review_concept(approved=true, user_confirmed_duration_sec=...) first."
    )


if __name__ == "__main__":
    maybe_start_metrics_server()
    mcp.run()
