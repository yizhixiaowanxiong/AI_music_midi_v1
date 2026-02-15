import logging
from typing import Any, Dict, List, Optional

from schema.arrangement import (
    GeneratedTrack,
    LastBarMidiSummary,
    LastBarTransitionNote,
    TrackContext,
    TrackGenRequest,
)
from schema.base import AgentRoutingRole
from summary.drum_summary import summarize_drums_for_bass
from utils.constants import KICK_PITCHES

logger = logging.getLogger(__name__)


def inject_context_if_needed(req: TrackGenRequest, ctx: Optional[TrackContext]) -> TrackGenRequest:
    """按角色把运行态上下文注入到请求 context（JIT 注入）。"""
    role = getattr(req, "instrument", None)
    if role not in (
        AgentRoutingRole.BASS,
        AgentRoutingRole.MELODY,
        AgentRoutingRole.HARMONY,
        AgentRoutingRole.FX,
    ):
        return req
    if ctx is None:
        return req

    req2 = req.model_copy(deep=True) if hasattr(req, "model_copy") else req.copy(deep=True)
    if req2.context is None:
        req2.context = TrackContext()

    source = ctx.model_copy(deep=True) if hasattr(ctx, "model_copy") else TrackContext(**dict(ctx))

    if role == AgentRoutingRole.BASS:
        req2.context.kick_onsets_ticks = list(source.kick_onsets_ticks or [])
        req2.context.kick_summary_text = source.kick_summary_text
        req2.context.break_ranges = source.break_ranges
        req2.context.kick_pitches = list(source.kick_pitches or [])
    elif role == AgentRoutingRole.MELODY:
        req2.context.chord_notes_per_bar = [list(xs) for xs in (source.chord_notes_per_bar or [])]
        req2.context.prev_section_last_bar_midi = source.prev_section_last_bar_midi
    elif role == AgentRoutingRole.HARMONY:
        req2.context.prev_section_last_bar_midi = source.prev_section_last_bar_midi
    elif role == AgentRoutingRole.FX:
        req2.context.lyric_rhythm_ticks = list(source.lyric_rhythm_ticks or [])

    return req2


def _compact_ticks(ticks: List[int], max_items: int = 64) -> List[int]:
    if not ticks:
        return []
    uniq = sorted(set(int(t) for t in ticks))
    if len(uniq) <= max_items:
        return uniq
    step = len(uniq) / max_items
    return [uniq[int(i * step)] for i in range(max_items)]


def _compact_notes(notes: List[int], max_notes: int = 4) -> List[int]:
    """保留最紧凑的一组和弦音（<= max_notes）。"""
    if not notes:
        return []
    uniq = sorted(set(int(n) for n in notes))
    if len(uniq) <= max_notes:
        return uniq

    best = uniq[:max_notes]
    best_span = best[-1] - best[0]
    for i in range(1, len(uniq) - max_notes + 1):
        window = uniq[i : i + max_notes]
        span = window[-1] - window[0]
        if span < best_span:
            best = window
            best_span = span
    return best


def extract_kick_summary(track: GeneratedTrack) -> Optional[Dict[str, Any]]:
    """提取给 Bass 使用的 Kick 摘要。"""
    raw = getattr(track, "raw_output", None)
    if raw is not None:
        try:
            summary = summarize_drums_for_bass(raw, mode="min")
            return {
                "kick_onsets_ticks": summary["kick_onsets_ticks"],
                "kick_summary_text": summary["llm_context_text"],
                "break_ranges": summary.get("break_ranges"),
                "kick_pitches": summary.get("kick_pitches"),
            }
        except Exception:
            logger.debug("summarize_drums_for_bass failed, fallback to note scan", exc_info=True)

    notes = getattr(track, "notes", None)
    if not notes:
        return None

    kick_onsets: List[int] = []
    try:
        for n in notes:
            pitch = getattr(n, "pitch", None)
            start = getattr(n, "start_tick", None)
            if pitch is None and isinstance(n, dict):
                pitch = n.get("pitch")
                start = n.get("start_tick")
            if pitch is None or start is None:
                continue
            if int(pitch) in KICK_PITCHES:
                kick_onsets.append(int(start))
    except Exception:
        logger.debug("kick fallback scan failed", exc_info=True)
        return None

    if not kick_onsets:
        return None

    return {
        "kick_onsets_ticks": kick_onsets,
        "kick_summary_text": f"Kick count={len(kick_onsets)} (fallback)",
        "kick_pitches": list(KICK_PITCHES),
    }


def extract_melody_summary(track: GeneratedTrack) -> Optional[Dict[str, Any]]:
    """提取 Melody 的起点节奏，供 FX 使用。"""
    notes = getattr(track, "notes", None)
    if not notes:
        raw = getattr(track, "raw_output", None)
        if raw is not None:
            try:
                from track_builder import flatten_section

                notes = flatten_section(raw)
            except Exception:
                logger.debug("flatten_section failed in extract_melody_summary", exc_info=True)
                notes = None
    if not notes:
        return None

    ticks: List[int] = []
    try:
        for n in notes:
            start = getattr(n, "start_tick", None)
            if start is None and isinstance(n, dict):
                start = n.get("start_tick")
            if start is None:
                continue
            ticks.append(int(start))
    except Exception:
        logger.debug("melody summary scan failed", exc_info=True)
        return None

    return {"lyric_rhythm_ticks": _compact_ticks(ticks, max_items=64)}


def extract_harmony_summary(track: GeneratedTrack) -> Optional[Dict[str, Any]]:
    """提取和声轨每小节和弦构成音。"""
    raw = getattr(track, "raw_output", None)
    if raw is None:
        return None

    bar_ticks = int(getattr(raw, "bar_ticks", 0) or 0)
    sec_start = getattr(raw, "section_start_bar", None)
    sec_end = getattr(raw, "section_end_bar", None)
    if not bar_ticks or sec_start is None or sec_end is None:
        return None

    total_bars = int(sec_end - sec_start + 1)
    if total_bars <= 0:
        return None

    notes = None
    try:
        from track_builder import flatten_section

        notes = flatten_section(raw)
    except Exception:
        logger.debug("flatten_section failed in extract_harmony_summary, fallback to track.notes", exc_info=True)
        notes = getattr(track, "notes", None)
    if not notes:
        return None

    chord_notes_per_bar: List[List[int]] = [[] for _ in range(total_bars)]
    try:
        for n in notes:
            pitch = getattr(n, "pitch", None)
            start = getattr(n, "start_tick", None)
            if pitch is None and isinstance(n, dict):
                pitch = n.get("pitch")
                start = n.get("start_tick")
            if pitch is None or start is None:
                continue
            bar_idx = int(int(start) // bar_ticks)
            if 0 <= bar_idx < total_bars:
                chord_notes_per_bar[bar_idx].append(int(pitch))
    except Exception:
        logger.debug("harmony summary scan failed", exc_info=True)
        return None

    return {"chord_notes_per_bar": [_compact_notes(xs, max_notes=4) for xs in chord_notes_per_bar]}


def _quantize_tick(tick: int, grid: int) -> int:
    return int(round(tick / grid) * grid)


def extract_last_bar_midi(
    track: GeneratedTrack,
    *,
    max_notes: int = 10,
    grid_div: int = 4,
) -> Optional[LastBarMidiSummary]:
    """提取最后一小节压缩音符摘要，供下一段衔接。"""
    raw = getattr(track, "raw_output", None)
    if raw is None:
        return None

    bar_ticks = int(getattr(raw, "bar_ticks", 0) or 0)
    sec_start = getattr(raw, "section_start_bar", None)
    sec_end = getattr(raw, "section_end_bar", None)
    if not bar_ticks or sec_start is None or sec_end is None:
        return None

    last_bar_start = int(sec_end - sec_start) * bar_ticks
    last_bar_end = last_bar_start + bar_ticks
    grid = max(1, int(bar_ticks / (grid_div * 4)))  # grid_div=4 => 1/16 网格

    notes = getattr(track, "notes", None)
    if not notes:
        try:
            from track_builder import flatten_section

            notes = flatten_section(raw)
        except Exception:
            logger.debug("flatten_section failed in extract_last_bar_midi", exc_info=True)
            notes = None
    if not notes:
        return None

    out_notes: List[Dict[str, int]] = []
    for n in notes:
        pitch = getattr(n, "pitch", None)
        start = getattr(n, "start_tick", None)
        velocity = getattr(n, "velocity", None)
        if pitch is None and isinstance(n, dict):
            pitch = n.get("pitch")
            start = n.get("start_tick")
            velocity = n.get("velocity")
        if start is None or pitch is None:
            continue

        s = int(start)
        if s < last_bar_start or s >= last_bar_end:
            continue
        q = _quantize_tick(s - last_bar_start, grid)
        out_notes.append(
            {
                "pitch_class": int(pitch) % 12,
                "start_tick": q,
                "velocity": int(velocity) if velocity is not None else 0,
            }
        )

    if not out_notes:
        return None

    # 先按力度保留 top-N，再去掉力度字段减小 payload
    out_notes.sort(key=lambda x: x.get("velocity", 0), reverse=True)
    out_notes = out_notes[:max_notes]
    compact_notes = [
        LastBarTransitionNote(
            pitch_class=int(n["pitch_class"]),
            start_tick=int(n["start_tick"]),
        )
        for n in out_notes
    ]

    return LastBarMidiSummary(
        track_key=str(getattr(track, "track_key", "unknown")),
        instrument=getattr(track, "instrument", None),
        notes=compact_notes,
        bar_ticks=bar_ticks,
    )
