from __future__ import annotations

from dataclasses import dataclass
from typing import List

from schema.drum_schema import DrumsSectionOutput
from schema.bass_schema import BassSectionOutput


@dataclass
class NoteEvent:
    pitch: int
    start_tick: int
    duration_tick: int
    velocity: int


@dataclass
class TrackOut:
    name: str
    instrument: str
    channel: int
    notes: List[NoteEvent]


def _compute_bar_ticks(time_signature: str, tpb: int) -> int:
    n, d = time_signature.split("/")
    return int(tpb * int(n) * (4 / int(d)))


def _pattern_len_bars(out, pattern) -> int:
    v = getattr(pattern, "pattern_len_bars", None)
    if isinstance(v, int) and v > 0:
        return v
    v = getattr(out, "pattern_len_bars", None)
    if isinstance(v, int) and v > 0:
        return v
    return 4


def flatten_section(out) -> List[NoteEvent]:
    """
    将 pattern + phrase 展开为绝对时间 NoteEvent 列表（相对 section_start_bar）
    """
    notes: List[NoteEvent] = []

    tpb = int(getattr(out, "ticks_per_beat", 480) or 480)
    ts = getattr(out, "time_signature", "4/4") or "4/4"
    bar_ticks = int(getattr(out, "bar_ticks", 0) or 0) or _compute_bar_ticks(ts, tpb)

    sec_start = int(out.section_start_bar)
    sec_end = int(out.section_end_bar)

    pattern_map = {p.tag: p for p in out.patterns}

    for ph in out.phrases:
        pat = pattern_map.get(ph.use_pattern_tag)
        if not pat:
            continue
        if ph.start_bar < sec_start or ph.end_bar > sec_end:
            continue

        phrase_len = int(ph.end_bar - ph.start_bar + 1)
        if phrase_len <= 0:
            continue

        pat_len = _pattern_len_bars(out, pat)
        phrase_abs_start = (int(ph.start_bar) - sec_start) * bar_ticks
        phrase_abs_end = phrase_abs_start + phrase_len * bar_ticks

        cur_bar = 0
        while cur_bar < phrase_len:
            loop_tick = phrase_abs_start + cur_bar * bar_ticks
            for n in pat.notes:
                abs_tick = loop_tick + int(n.start_tick)
                if abs_tick >= phrase_abs_end:
                    continue
                notes.append(NoteEvent(
                    pitch=int(n.pitch),
                    start_tick=abs_tick,
                    duration_tick=int(n.duration_tick),
                    velocity=int(n.velocity),
                ))
            cur_bar += max(1, pat_len)

    notes.sort(key=lambda x: x.start_tick)
    return notes


def drums_to_track(out: DrumsSectionOutput, name: str = "Drums", channel: int = 9) -> TrackOut:
    return TrackOut(name=name, instrument="drums", channel=channel, notes=flatten_section(out))


def bass_to_track(out: BassSectionOutput, name: str = "Bass", channel: int = 1) -> TrackOut:
    return TrackOut(name=name, instrument="bass", channel=channel, notes=flatten_section(out))
