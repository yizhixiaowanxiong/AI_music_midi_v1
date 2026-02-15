from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from schema.base import NoteEvent


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_time_signature(ts: str) -> Tuple[int, int]:
    try:
        n, d = str(ts).split("/")
        return int(n), int(d)
    except Exception:
        return 4, 4


def _compute_bar_ticks(time_signature: str, ticks_per_beat: int) -> int:
    n, d = _parse_time_signature(time_signature or "4/4")
    return int(ticks_per_beat * n * (4 / d))


def _pattern_len_bars(out: Any, pattern: Any) -> int:
    value = _get(pattern, "pattern_len_bars", None)
    if isinstance(value, int) and value > 0:
        return value
    value = _get(pattern, "len_bars", None)
    if isinstance(value, int) and value > 0:
        return value
    value = _get(out, "pattern_len_bars", None)
    if isinstance(value, int) and value > 0:
        return value
    return 4


def _append_pattern_notes(
    target: List[NoteEvent],
    pattern: Any,
    loop_abs_start_tick: int,
    phrase_abs_end_tick: int,
) -> None:
    for note in _get(pattern, "notes", []) or []:
        start_tick = int(_get(note, "start_tick", 0) or 0)
        duration_tick = int(_get(note, "duration_tick", 0) or 0)
        abs_tick = loop_abs_start_tick + start_tick
        if abs_tick >= phrase_abs_end_tick:
            continue
        target.append(
            NoteEvent(
                pitch=int(_get(note, "pitch", 0) or 0),
                start_tick=abs_tick,
                duration_tick=max(1, duration_tick),
                velocity=int(_get(note, "velocity", 90) or 90),
            )
        )


def flatten_section(out: Any) -> List[NoteEvent]:
    patterns = _get(out, "patterns", []) or []
    phrases = _get(out, "phrases", []) or []
    if not patterns or not phrases:
        return []

    pattern_map: Dict[str, Any] = {_get(p, "tag", "core"): p for p in patterns}
    tpb = int(_get(out, "ticks_per_beat", 480) or 480)
    ts = _get(out, "time_signature", "4/4") or "4/4"
    bar_ticks = int(_get(out, "bar_ticks", 0) or 0) or _compute_bar_ticks(ts, tpb)
    sec_start = int(_get(out, "section_start_bar", 1) or 1)
    sec_end = int(_get(out, "section_end_bar", sec_start) or sec_start)

    rendered: List[NoteEvent] = []
    for phrase in phrases:
        tag = str(_get(phrase, "use_pattern_tag", "core"))
        pattern = pattern_map.get(tag)
        if pattern is None:
            continue

        phrase_start_bar = int(_get(phrase, "start_bar", sec_start) or sec_start)
        phrase_end_bar = int(_get(phrase, "end_bar", phrase_start_bar) or phrase_start_bar)
        if phrase_start_bar < sec_start or phrase_end_bar > sec_end:
            continue
        phrase_len_bars = phrase_end_bar - phrase_start_bar + 1
        if phrase_len_bars <= 0:
            continue

        phrase_abs_start = (phrase_start_bar - sec_start) * bar_ticks
        phrase_abs_end = phrase_abs_start + phrase_len_bars * bar_ticks

        pattern_len_bars = max(1, _pattern_len_bars(out, pattern))
        cur_bar = 0
        while cur_bar < phrase_len_bars:
            loop_start_tick = phrase_abs_start + cur_bar * bar_ticks
            _append_pattern_notes(rendered, pattern, loop_start_tick, phrase_abs_end)
            cur_bar += pattern_len_bars

        fill_tag = _get(phrase, "end_fill_tag", None)
        if fill_tag:
            fill_pattern = pattern_map.get(str(fill_tag))
            if fill_pattern is not None:
                fill_len_bars = max(1, _pattern_len_bars(out, fill_pattern))
                fill_start = max(phrase_abs_start, phrase_abs_end - fill_len_bars * bar_ticks)
                _append_pattern_notes(rendered, fill_pattern, fill_start, phrase_abs_end)

    rendered.sort(key=lambda n: n.start_tick)
    return rendered


@dataclass
class TrackOut:
    name: str
    instrument: str
    channel: int
    notes: List[NoteEvent] = field(default_factory=list)


def drums_to_track(out: Any) -> TrackOut:
    section_name = str(_get(out, "section_name", "Section"))
    return TrackOut(
        name=f"{section_name}_drums",
        instrument="drums",
        channel=9,
        notes=flatten_section(out),
    )


def bass_to_track(out: Any) -> TrackOut:
    section_name = str(_get(out, "section_name", "Section"))
    return TrackOut(
        name=f"{section_name}_bass",
        instrument="bass",
        channel=1,
        notes=flatten_section(out),
    )

