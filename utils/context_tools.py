"""
Context extraction/compression utilities for prompt injection.

This module accepts raw or semi-structured runtime data and produces
stable, compact context text for LLM prompts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from schema.base import NoteEvent
from schema.request import (
    GeneratedTrack,
    LastBarMidiSummary,
    LastBarTransitionNote,
    TrackGenRequest,
)

try:
    from utils.constants import KICK_PITCHES
except Exception:
    KICK_PITCHES = (35, 36)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return max(min_value, int(default))
    try:
        value = int(raw)
    except ValueError:
        value = int(default)
    return max(min_value, value)


def _to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return dict(value.model_dump())
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        try:
            return dict(vars(value))
        except Exception:
            return {}
    return {}


def _get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _role_text(value: Any) -> str:
    out = str(getattr(value, "value", value) or "").strip().lower()
    if out == "drums":
        return "percussion"
    return out


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clip_chars(text: str, max_chars: int) -> str:
    clean = str(text or "")
    if max_chars <= 0:
        return ""
    if len(clean) <= max_chars:
        return clean
    if max_chars <= 3:
        return clean[:max_chars]
    return clean[: max_chars - 3] + "..."


def _as_note(item: Any) -> Optional[NoteEvent]:
    if isinstance(item, NoteEvent):
        return item
    if isinstance(item, dict):
        try:
            return NoteEvent(**item)
        except Exception:
            return None
    try:
        return NoteEvent(
            pitch=int(getattr(item, "pitch")),
            start_tick=int(getattr(item, "start_tick")),
            duration_tick=int(getattr(item, "duration_tick")),
            velocity=int(getattr(item, "velocity", 100)),
        )
    except Exception:
        return None


def _as_note_list(notes: Any) -> List[NoteEvent]:
    out: List[NoteEvent] = []
    for raw in list(notes or []):
        item = _as_note(raw)
        if item is not None:
            out.append(item)
    return out


def estimate_text_tokens(text: str) -> int:
    """
    Lightweight token estimation for budget clipping.
    """
    s = str(text or "")
    if not s:
        return 0
    words = len(re.findall(r"[A-Za-z0-9_]+", s))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    punct = len(re.findall(r"[^\w\s]", s))
    rough = max((len(s) + 3) // 4, words + cjk + punct // 2)
    return max(1, int(rough))


def _clip_text_to_token_budget(text: str, budget: int) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if budget <= 0:
        return ""
    if estimate_text_tokens(s) <= budget:
        return s

    low = 0
    high = len(s)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = _clip_chars(s, mid)
        if estimate_text_tokens(candidate) <= budget:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best.strip()


def compact_text(value: Any, *, max_chars: int = 160) -> str:
    text = _normalize_space(str(value or ""))
    return _clip_chars(text, max_chars=max_chars)


def compact_scale_name(root: Any, scale: Any) -> str:
    root_text = compact_text(root, max_chars=12)
    if isinstance(scale, dict):
        scale_root = compact_text(scale.get("root"), max_chars=12)
        scale_name = compact_text(scale.get("name"), max_chars=24)
        if scale_root and scale_name:
            return f"{scale_root} {scale_name}"
        if scale_name:
            return scale_name
    scale_text = compact_text(scale, max_chars=36)
    if root_text and scale_text and root_text.lower() not in scale_text.lower():
        return f"{root_text} {scale_text}"
    return scale_text or root_text


def compact_chords(chords: Any, *, max_items: int = 8) -> List[str]:
    out: List[str] = []
    for item in list(chords or [])[: max(0, int(max_items))]:
        text = compact_text(item, max_chars=24)
        if text:
            out.append(text)
    return out


def compact_design(design: Any, *, max_keys: int = 10) -> Dict[str, Any]:
    payload = _to_dict(design)
    if not payload:
        return {}

    preferred = [
        "role",
        "instrument_name",
        "playing_style",
        "timbre",
        "register",
        "density",
        "rhythm_role",
        "stereo",
        "articulation",
        "note_length",
    ]
    out: Dict[str, Any] = {}
    for key in preferred:
        if key in payload:
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                out[key] = value
            else:
                out[key] = compact_text(value, max_chars=96)
        if len(out) >= max_keys:
            break

    if not out:
        for key in list(payload.keys())[: max(1, int(max_keys))]:
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                out[str(key)] = value
            else:
                out[str(key)] = compact_text(value, max_chars=96)
    return out


def compact_int_list(values: Any, *, max_items: int = 24) -> List[int]:
    seen = set()
    out: List[int] = []
    for raw in list(values or []):
        try:
            value = int(raw)
        except Exception:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= max(0, int(max_items)):
            break
    return out


def compact_harmony_notes_per_bar(
    harmony_notes: Any,
    *,
    max_bars: int = 8,
    max_notes_per_bar: int = 4,
) -> List[List[int]]:
    out: List[List[int]] = []
    for bar in list(harmony_notes or [])[: max(0, int(max_bars))]:
        out.append(compact_int_list(bar, max_items=max_notes_per_bar))
    return out


def compact_prev_section_midi(
    prev_section_midi: Any,
    *,
    max_tracks: int = 2,
    max_notes_per_track: int = 6,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in list(prev_section_midi or [])[: max(0, int(max_tracks))]:
        item = _to_dict(raw)
        track_key = compact_text(item.get("track_key"), max_chars=48)
        instrument = compact_text(item.get("instrument"), max_chars=24)
        bar_ticks = int(item.get("bar_ticks") or 0)
        notes = item.get("notes") or []
        compact_notes: List[Dict[str, int]] = []
        for note_raw in list(notes)[: max(0, int(max_notes_per_track))]:
            note = _to_dict(note_raw)
            try:
                pitch_class = int(note.get("pitch_class"))
                start_tick = int(note.get("start_tick"))
            except Exception:
                continue
            compact_notes.append({"pitch_class": pitch_class, "start_tick": start_tick})
        out.append(
            {
                "track_key": track_key,
                "instrument": instrument,
                "bar_ticks": bar_ticks,
                "notes": compact_notes,
            }
        )
    return out


def section_output_contract(track_key: str) -> Dict[str, Any]:
    return {
        "track_key": str(track_key or ""),
        "required": ["flat_notes|patterns"],
        "note_schema": {
            "pitch": "int[0..127]",
            "start_tick": "int>=0",
            "duration_tick": "int>0",
            "velocity": "int[0..127]",
        },
    }


def _ensure_tag(text: str, tag: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if s.startswith("["):
        return s
    return f"{tag} {s}"


def compose_context_summary(
    *,
    global_anchor_summary: str = "",
    section_summary: str = "",
    avoidance_summary: str = "",
    token_budget: Optional[int] = None,
) -> str:
    parts = [
        _ensure_tag(global_anchor_summary, "[GLOBAL_ANCHOR]"),
        _ensure_tag(section_summary, "[SECTION]"),
        _ensure_tag(avoidance_summary, "[AVOIDANCE]"),
    ]
    merged = "\n".join([p for p in parts if p]).strip()
    budget = token_budget if token_budget is not None else _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", 300, 1)
    return _clip_text_to_token_budget(merged, int(budget))


def context_layer_payload(
    *,
    global_anchor_summary: str = "",
    section_summary: str = "",
    context_summary: str = "",
    avoidance_rule_text: str = "",
) -> Dict[str, str]:
    budget = _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", 300, 1)
    if str(context_summary or "").strip():
        return {"context_summary": _clip_text_to_token_budget(str(context_summary), budget)}
    merged = compose_context_summary(
        global_anchor_summary=global_anchor_summary,
        section_summary=section_summary,
        avoidance_summary=avoidance_rule_text,
        token_budget=budget,
    )
    if not merged:
        return {}
    return {"context_summary": merged}


def log_context_budget(
    *,
    logger: logging.Logger,
    agent: str,
    track_key: str,
    section_name: str,
    global_anchor_summary: str = "",
    section_summary: str = "",
    avoidance_rule_text: str = "",
    context_summary: str = "",
) -> Dict[str, Any]:
    budget = _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", 300, 1)
    global_anchor_tokens = estimate_text_tokens(global_anchor_summary)
    section_tokens = estimate_text_tokens(section_summary)
    avoidance_tokens = estimate_text_tokens(avoidance_rule_text)
    context_tokens = estimate_text_tokens(context_summary)
    merged = compose_context_summary(
        global_anchor_summary=global_anchor_summary,
        section_summary=section_summary,
        avoidance_summary=avoidance_rule_text,
        token_budget=budget,
    )
    context_layer_tokens = estimate_text_tokens(merged)
    total = max(context_tokens, context_layer_tokens)
    over = 1 if total > budget else 0

    out = {
        "global_anchor_tokens": int(global_anchor_tokens),
        "section_tokens": int(section_tokens),
        "avoidance_tokens": int(avoidance_tokens),
        "context_tokens": int(context_tokens),
        "context_layer_tokens": int(context_layer_tokens),
        "context_total_tokens": int(total),
        "token_budget": int(budget),
        "over_budget": int(over),
    }
    try:
        logger.info(
            "[context_budget] agent=%s track=%s section=%s total=%s budget=%s over=%s",
            str(agent or ""),
            str(track_key or ""),
            str(section_name or ""),
            out["context_total_tokens"],
            out["token_budget"],
            out["over_budget"],
        )
    except Exception:
        pass
    return out


@dataclass
class GlobalMusicAnchorView:
    bpm: int = 120
    key: str = "C"
    scale: str = "Minor"
    time_signature: str = "4/4"
    total_bars: int = 8
    chord_progression: List[str] = field(default_factory=list)
    style_tags: List[str] = field(default_factory=list)
    mood: str = ""
    summary: str = ""


def _split_style_tags(style_description: str, mood: str) -> List[str]:
    raw = f"{style_description or ''},{mood or ''}"
    parts = re.split(r"[/,|]", raw)
    out: List[str] = []
    for part in parts:
        text = compact_text(part, max_chars=24)
        if text and text not in out:
            out.append(text)
    return out[:6]


def build_global_music_anchor(
    *,
    concept: Any,
    total_bars: int,
    chord_progression: Optional[List[str]] = None,
) -> GlobalMusicAnchorView:
    bpm = int(_get_attr(concept, "bpm", default=120) or 120)
    key_root = compact_text(_get_attr(concept, "key_root", "key", default="C"), max_chars=8) or "C"
    key_mode = compact_text(_get_attr(concept, "key_mode", "scale", default="Minor"), max_chars=16) or "Minor"
    time_signature = compact_text(_get_attr(concept, "time_signature", default="4/4"), max_chars=8) or "4/4"
    style_description = compact_text(_get_attr(concept, "style_description", default=""), max_chars=96)
    mood = compact_text(_get_attr(concept, "mood", default=""), max_chars=48)
    style_tags = _split_style_tags(style_description, mood)
    chords = compact_chords(chord_progression or [], max_items=6)
    bars = max(1, int(total_bars))

    style_text = "/".join(style_tags) if style_tags else compact_text(style_description or mood, max_chars=48)
    chord_text = f" | Chords:{'->'.join(chords)}" if chords else ""
    summary = (
        f"[GLOBAL_ANCHOR] {bpm}BPM {key_root}{key_mode} {time_signature} | "
        f"{bars} bars | {style_text or 'style'}{chord_text}"
    )
    summary = _clip_text_to_token_budget(summary, _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", 300, 1))

    return GlobalMusicAnchorView(
        bpm=bpm,
        key=key_root,
        scale=key_mode,
        time_signature=time_signature,
        total_bars=bars,
        chord_progression=chords,
        style_tags=style_tags,
        mood=mood,
        summary=summary,
    )


def build_concept_summaries(concept: Any) -> Dict[str, str]:
    bpm = int(_get_attr(concept, "bpm", default=120) or 120)
    key_root = compact_text(_get_attr(concept, "key_root", default="C"), max_chars=8) or "C"
    key_mode = compact_text(_get_attr(concept, "key_mode", default="Minor"), max_chars=16) or "Minor"
    time_signature = compact_text(_get_attr(concept, "time_signature", default="4/4"), max_chars=8) or "4/4"
    style_description = compact_text(_get_attr(concept, "style_description", default=""), max_chars=96)
    mood = compact_text(_get_attr(concept, "mood", default=""), max_chars=64)
    target_duration_sec = int(_get_attr(concept, "target_duration_sec", default=60) or 60)

    short = (
        f"[CONCEPT] {style_description or 'Style'} | "
        f"{bpm}BPM | {key_root} {key_mode} | {time_signature} | "
        f"{target_duration_sec}s"
    )
    if mood:
        short = f"{short} | Mood:{mood}"

    long_lines = [
        "[CONCEPT_LONG]",
        f"Style: {style_description or 'N/A'}",
        f"Mood: {mood or 'N/A'}",
        f"Tempo: {bpm} BPM",
        f"Time Signature: {time_signature}",
        f"Key: {key_root} {key_mode}",
        f"Target Duration: {target_duration_sec} sec",
    ]
    long = "\n".join(long_lines)
    return {"short": short, "long": long}


def build_blueprint_summaries(blueprint: Any) -> Dict[str, str]:
    sections = list(_get_attr(blueprint, "sections", default=[]) or [])
    total_bars = 0
    names: List[str] = []
    details: List[str] = []

    for idx, section in enumerate(sections):
        name = compact_text(_get_attr(section, "name", default=f"Section-{idx}"), max_chars=24) or f"Section-{idx}"
        bars = max(1, int(_get_attr(section, "length_bars", default=1) or 1))
        energy = max(1, min(5, int(_get_attr(section, "energy_level", default=3) or 3)))
        chords = compact_chords(_get_attr(section, "chord_progression", default=[]), max_items=4)
        chord_text = "->".join(chords)
        if chord_text and len(list(_get_attr(section, "chord_progression", default=[]) or [])) > 4:
            chord_text = f"{chord_text}->..."

        total_bars += bars
        names.append(name)
        row = f"{idx}. {name} | {bars} bars | E{energy}/5"
        if chord_text:
            row = f"{row} | {chord_text}"
        details.append(row)

    head = "/".join(names[:4])
    if len(names) > 4:
        head = f"{head}/..."

    short = f"[BLUEPRINT] {len(sections)} sections | {total_bars} bars"
    if head:
        short = f"{short} | {head}"

    long_lines = [
        "[BLUEPRINT_LONG]",
        f"Sections: {len(sections)}",
        f"Total Bars: {total_bars}",
    ]
    long_lines.extend(details[:16])
    long = "\n".join(long_lines)
    return {"short": short, "long": long}


def build_section_summary(
    *,
    section_name: str,
    start_bar: int,
    end_bar: int,
    section_energy: float,
    arrangement_size: int,
    transition_to_next: str = "",
    section_energy_level: Optional[int] = None,
    section_function: Optional[str] = None,
) -> str:
    bars = max(1, int(end_bar) - int(start_bar) + 1)
    level = section_energy_level
    if level is None:
        try:
            level = int(round(float(section_energy) * 4.0 + 1.0))
        except Exception:
            level = 3
    level = max(1, min(5, int(level)))

    density = "low"
    if arrangement_size >= 5:
        density = "high"
    elif arrangement_size >= 3:
        density = "mid"

    parts = [
        f"{compact_text(section_name, max_chars=32) or 'Section'} {int(start_bar)}-{int(end_bar)} bars",
        f"Energy:{level}/5",
        f"Density:{density}",
        f"Tracks:{max(1, int(arrangement_size))}",
    ]
    if section_function:
        parts.append(f"Func:{compact_text(section_function, max_chars=24)}")
    if transition_to_next:
        parts.append(f"Transition:{compact_text(transition_to_next, max_chars=32)}")
    text = " | ".join(parts)
    return _ensure_tag(text, "[SECTION]")


def _track_role(track: Any) -> str:
    return _role_text(_get_attr(track, "instrument", "role", default=""))


def _pick_tracks_by_role(prev_tracks: Mapping[str, Any], role: str) -> List[GeneratedTrack]:
    target = _role_text(role)
    out: List[GeneratedTrack] = []
    for key, track in dict(prev_tracks or {}).items():
        role_text = _track_role(track)
        key_text = _role_text(str(key))
        if role_text == target:
            out.append(track)
            continue
        if target in key_text:
            out.append(track)
            continue
        if target == "percussion" and "drums" in key_text:
            out.append(track)
    return out


def extract_kick_summary(track: GeneratedTrack) -> Dict[str, Any]:
    notes = _as_note_list(_get_attr(track, "notes", default=[]))
    onsets = sorted({int(n.start_tick) for n in notes if int(n.pitch) in tuple(KICK_PITCHES)})
    if not onsets:
        return {}
    return {
        "kick_onsets_ticks": onsets[:64],
        "locked_rhythm_rules": [
            "align core accents to kick onsets",
            "avoid same-tick overlap on kick accents",
        ],
    }


def _infer_bar_ticks(track: Any) -> int:
    raw = _get_attr(track, "raw_output", default=None)
    bar_ticks = int(_get_attr(raw, "bar_ticks", default=0) or 0)
    if bar_ticks > 0:
        return bar_ticks
    time_signature = compact_text(_get_attr(raw, "time_signature", default="4/4"), max_chars=8) or "4/4"
    try:
        n, d = str(time_signature).split("/")
        return int(480 * int(n) * (4 / int(d)))
    except Exception:
        return 1920


def extract_harmony_summary(track: GeneratedTrack) -> Dict[str, Any]:
    notes = _as_note_list(_get_attr(track, "notes", default=[]))
    if not notes:
        return {}
    bar_ticks = _infer_bar_ticks(track)
    grouped: Dict[int, List[int]] = {}
    for note in notes:
        bar_idx = int(note.start_tick) // max(1, int(bar_ticks))
        grouped.setdefault(bar_idx, []).append(int(note.pitch) % 12)
    chord_notes_per_bar: List[List[int]] = []
    for bar_idx in sorted(grouped.keys())[:8]:
        uniq = sorted(set(grouped[bar_idx]))
        chord_notes_per_bar.append(uniq[:4])
    if not chord_notes_per_bar:
        return {}
    return {"chord_notes_per_bar": chord_notes_per_bar}


def extract_melody_summary(track: GeneratedTrack) -> Dict[str, Any]:
    notes = _as_note_list(_get_attr(track, "notes", default=[]))
    if not notes:
        return {}
    onsets = sorted({int(n.start_tick) for n in notes})
    return {"lyric_rhythm_ticks": onsets[:64]}


def extract_last_bar_midi(
    track: GeneratedTrack,
    *,
    max_notes: int = 10,
    grid_div: int = 4,
) -> Optional[LastBarMidiSummary]:
    notes = _as_note_list(_get_attr(track, "notes", default=[]))
    if not notes:
        return None
    bar_ticks = _infer_bar_ticks(track)

    raw = _get_attr(track, "raw_output", default=None)
    end_bar = int(_get_attr(raw, "section_end_bar", default=0) or 0)
    if end_bar <= 0:
        max_tick = max(int(n.start_tick) for n in notes)
        end_bar = int(max_tick // max(1, bar_ticks)) + 1
    last_bar_start = max(0, (end_bar - 1) * max(1, bar_ticks))
    last_bar_end = last_bar_start + max(1, bar_ticks)
    picked = [n for n in notes if last_bar_start <= int(n.start_tick) < last_bar_end]
    if not picked:
        latest_tick = max(int(n.start_tick) for n in notes)
        picked = [n for n in notes if int(n.start_tick) == latest_tick]

    step = max(1, int(bar_ticks) // max(1, int(grid_div or 1)))
    trimmed: List[LastBarTransitionNote] = []
    for note in sorted(picked, key=lambda n: int(n.start_tick))[: max(1, int(max_notes))]:
        start_tick = int(note.start_tick)
        if grid_div and int(grid_div) > 0:
            start_tick = int(round(start_tick / step) * step)
        trimmed.append(
            LastBarTransitionNote(
                pitch_class=int(note.pitch) % 12,
                start_tick=max(0, start_tick),
            )
        )

    if not trimmed:
        return None

    instrument = _role_text(_get_attr(track, "instrument", default=""))
    return LastBarMidiSummary(
        track_key=str(_get_attr(track, "track_key", default="") or ""),
        instrument=instrument,
        notes=trimmed,
        bar_ticks=int(bar_ticks),
    )


def _avoidance_from_prev_tracks(req: TrackGenRequest, prev_tracks: Mapping[str, Any]) -> str:
    role = _role_text(_get_attr(req, "instrument", "role", default=""))
    if not role:
        return ""

    if role == "bass":
        percussion_tracks = _pick_tracks_by_role(prev_tracks, "percussion")
        if percussion_tracks:
            summary = extract_kick_summary(percussion_tracks[0])
            onsets = summary.get("kick_onsets_ticks") or []
            if onsets:
                sample = ",".join(str(x) for x in onsets[:8])
                return f"[AVOIDANCE] Rhythm:align_to_kick({sample})"

    if role == "melody":
        harmony_tracks = _pick_tracks_by_role(prev_tracks, "harmony")
        if harmony_tracks:
            summary = extract_harmony_summary(harmony_tracks[0])
            notes_per_bar = summary.get("chord_notes_per_bar") or []
            if notes_per_bar:
                head = notes_per_bar[0]
                return f"[AVOIDANCE] HarmonyToneRef:{head}"

    if role == "fx":
        melody_tracks = _pick_tracks_by_role(prev_tracks, "melody")
        if melody_tracks:
            summary = extract_melody_summary(melody_tracks[0])
            onsets = summary.get("lyric_rhythm_ticks") or []
            if onsets:
                return "[AVOIDANCE] FX align near melody phrase boundaries"

    return ""


def build_context_summary(
    req: TrackGenRequest,
    prev_tracks: Optional[Mapping[str, Any]] = None,
) -> str:
    bpm = int(_get_attr(req, "bpm", default=120) or 120)
    key = compact_text(_get_attr(req, "key", "key_root", default="C"), max_chars=8) or "C"
    scale = compact_text(_get_attr(req, "scale", "key_mode", default="Minor"), max_chars=16) or "Minor"
    style = compact_text(_get_attr(req, "style_context", "style_description", default=""), max_chars=96)
    start_bar = int(_get_attr(req, "start_bar", default=1) or 1)
    end_bar = int(_get_attr(req, "end_bar", default=start_bar) or start_bar)
    section_name = compact_text(_get_attr(req, "section_name", default="Section"), max_chars=32) or "Section"
    energy = _get_attr(req, "energy", "energy_level", default=3)
    try:
        energy_level = int(round(float(energy) * 4 + 1)) if isinstance(energy, float) and energy <= 1.0 else int(energy)
    except Exception:
        energy_level = 3
    energy_level = max(1, min(5, energy_level))
    chords = compact_chords(_get_attr(req, "chords", "chord_progression", default=[]), max_items=6)

    global_anchor = f"[GLOBAL_ANCHOR] {bpm}BPM {key}{scale}"
    if style:
        global_anchor += f" | {style}"
    if chords:
        global_anchor += f" | Chords:{'->'.join(chords)}"

    section = (
        f"[SECTION] {section_name} {start_bar}-{end_bar} bars | Energy:{energy_level}/5"
    )

    avoidance = ""
    if isinstance(prev_tracks, Mapping) and prev_tracks:
        avoidance = _avoidance_from_prev_tracks(req, prev_tracks)

    return compose_context_summary(
        global_anchor_summary=global_anchor,
        section_summary=section,
        avoidance_summary=avoidance,
    )


def inject_context_if_needed(req: TrackGenRequest, context_data: Optional[Mapping[str, Any]]) -> TrackGenRequest:
    req.context_summary = build_context_summary(req, context_data or {})
    return req
