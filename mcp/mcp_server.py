from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastmcp import FastMCP, Context
from fastmcp.server.dependencies import get_http_headers

from agents.director_agent import DirectorAgent
from agents.drums_agent import DrumsAgent
from agents.bass_agent import BassAgent
from summary.drum_summary import summarize_drums_for_bass
from track_builder import drums_to_track, bass_to_track
from midi_renderer import render_tracks_to_midi
from schema.blueprint_schema import SongBlueprint, Section
from schema.blueprint_skeleton_schema import SongBlueprintSkeleton


mcp = FastMCP(name="AiAgentMusic MCP")


@dataclass
class MusicSession:
    skeleton: Optional[SongBlueprintSkeleton] = None
    blueprint: Optional[SongBlueprint] = None
    section_details: Dict[int, Section] = field(default_factory=dict)
    drums_sections: Dict[int, object] = field(default_factory=dict)
    bass_sections: Dict[int, object] = field(default_factory=dict)
    last_section_idx: Optional[int] = None


_SESSIONS: Dict[str, MusicSession] = {}
_DEFAULT_SESSION_ID = "default"

# 获取上下文的session_id
def _resolve_session_id(ctx: Optional[Context]) -> str:
    headers = get_http_headers(include_all=True) or {}
    for key in ("session-id", "session_id", "mcp-session-id", "x-session-id"):
        val = headers.get(key)
        if val:
            return val
    if ctx and getattr(ctx, "session_id", None):
        return ctx.session_id
    return _DEFAULT_SESSION_ID

# 返回对应id会话
def _get_session(ctx: Optional[Context]) -> MusicSession:
    sid = _resolve_session_id(ctx)
    if sid not in _SESSIONS:
        _SESSIONS[sid] = MusicSession()
    return _SESSIONS[sid]

# 构建完整蓝图
def _try_build_full_blueprint(session: MusicSession) -> None:
    if not session.skeleton:
        return
    if len(session.section_details) != len(session.skeleton.sections):
        return
    sections = []
    for idx in range(len(session.skeleton.sections)):
        sec = session.section_details.get(idx)
        if not sec:
            return
        sections.append(sec)

    session.blueprint = SongBlueprint(
        song_name=session.skeleton.song_name,
        style_description=session.skeleton.style_description,
        bpm=session.skeleton.bpm,
        time_signature=session.skeleton.time_signature,
        root_note=session.skeleton.root_note,
        scale=session.skeleton.scale,
        total_bars=session.skeleton.total_bars,
        groove_global=session.skeleton.groove_global.model_dump(),
        sections=sections,
        global_constraints=[],
    )

# 将构建好的 SongBlueprint 对象转为字符串
def _format_blueprint_report(bp: SongBlueprint) -> str:
    lines = []
    lines.append(f"{bp.song_name}")
    lines.append(f"Style: {bp.style_description}")
    lines.append(
        f"BPM: {bp.bpm} | Key: {bp.root_note} {bp.scale} | "
        f"Bars: {bp.total_bars} | TS: {bp.time_signature}"
    )
    lines.append("-" * 90)
    for s in bp.sections:
        ln = s.end_bar - s.start_bar + 1
        chord_str = " ".join(s.chord_progression)
        lines.append(
            f"[{s.name:<10}] bars {s.start_bar:>2}-{s.end_bar:<2} "
            f"(len={ln:>2})  energy={s.global_energy:.2f}  "
            f"chord_rhythm={s.chord_rhythm}"
        )
        lines.append(f"  chords: {chord_str}")
        for k, inst in s.arrangement.items():
            lines.append(
                f"  - {k:<6} role={inst.role:<10} "
                f"var={inst.variant_tag:<5} e={inst.energy_level:.2f} | "
                f"{inst.playing_style}"
            )
        lines.append("")
    lines.append("QC: Pydantic validation passed")
    return "\n".join(lines)

# 基本骨架生成
@mcp.tool
def create_blueprint_skeleton(user_request: str, ctx: Context) -> dict:
    session = _get_session(ctx)
    director = DirectorAgent()
    skeleton = director.generate_skeleton(user_request)
    session.skeleton = skeleton
    session.blueprint = None
    session.section_details = {}
    session.drums_sections = {}
    session.bass_sections = {}
    session.last_section_idx = None
    return skeleton.model_dump()

# 细节填充
@mcp.tool
def enrich_section_details(section_index: int, ctx: Context = None) -> dict:
    session = _get_session(ctx)
    if not session.skeleton:
        raise ValueError("No skeleton in session. Call create_blueprint_skeleton first.")
    director = DirectorAgent()
    section = director.enrich_section(session.skeleton, section_index)
    session.section_details[section_index] = section
    session.last_section_idx = section_index
    _try_build_full_blueprint(session)
    return section.model_dump()

# drums音符生成
@mcp.tool
def generate_drums(section_index: int, strictness: int = 1, ctx: Context = None) -> dict:
    session = _get_session(ctx)
    if not session.skeleton:
        raise ValueError("No skeleton in session. Call create_blueprint_skeleton first.")
    if section_index < 0 or section_index >= len(session.skeleton.sections):
        raise ValueError("section_index out of range.")
    section = session.section_details.get(section_index)
    if not section:
        raise ValueError("Section details missing. Call enrich_section_details first.")
    agent = DrumsAgent()
    drums = agent.generate_for_section(session.skeleton, section, strictness=strictness)
    session.drums_sections[section_index] = drums
    session.last_section_idx = section_index
    return drums.model_dump()

# bass音符生成
@mcp.tool
def generate_bass(section_index: int, strictness: int = 1, ctx: Context = None) -> dict:
    session = _get_session(ctx)
    if not session.skeleton:
        raise ValueError("No skeleton in session. Call create_blueprint_skeleton first.")
    drums = session.drums_sections.get(section_index)
    if not drums:
        raise ValueError("No drums for this section. Call generate_drums first.")
    section = session.section_details.get(section_index)
    if not section:
        raise ValueError("Section details missing. Call enrich_section_details first.")
    summary = summarize_drums_for_bass(drums, mode="min")
    agent = BassAgent()
    bass = agent.generate_bass_for_section(
        session.skeleton, section, summary, strictness=strictness
    )
    session.bass_sections[section_index] = bass
    session.last_section_idx = section_index
    return bass.model_dump()

# midi渲染
@mcp.tool
def export_midi(
    section_index: Optional[int] = None,
    out_filename: Optional[str] = None,
    strictness: int = 1,
    ctx: Context = None,
) -> dict:
    session = _get_session(ctx)
    if not session.skeleton:
        raise ValueError("No skeleton in session. Call create_blueprint_skeleton first.")
    if section_index is None:
        if session.last_section_idx is None:
            raise ValueError("No section generated yet. Provide section_index.")
        section_index = session.last_section_idx

    drums = session.drums_sections.get(section_index)
    bass = session.bass_sections.get(section_index)
    if not drums and not bass:
        raise ValueError("No tracks for this section. Generate drums/bass first.")

    tracks = []
    if drums:
        tracks.append(drums_to_track(drums))
    if bass:
        tracks.append(bass_to_track(bass))

    out_dir = Path("data/midi_all")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not out_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_filename = f"mcp_section_{section_index}_{ts}.mid"
    out_path = str(out_dir / out_filename)

    render_tracks_to_midi(
        tracks,
        bpm=session.skeleton.bpm,
        out_path=out_path,
        strictness=strictness,
    )
    return {"out_path": out_path, "tracks": [t.instrument for t in tracks]}

# MCP资源提供，让 AI 随时了解当前的编曲状态
@mcp.resource("music://current/blueprint")
def current_blueprint(ctx: Context = None) -> str:
    session = _get_session(ctx)
    if session.blueprint:
        return _format_blueprint_report(session.blueprint)
    if session.skeleton:
        return session.skeleton.model_dump_json(indent=2)
    return "No blueprint available in this session."


if __name__ == "__main__":
    mcp.run()
