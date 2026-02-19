"""Dispatch helpers: convert sections into executable TrackGenRequest items."""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from schema.base import AgentRoutingRole
from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept
from schema.request import TrackGenRequest
from utils.constants import TPB
from utils.track_dispatch_config import TRACK_DISPATCH_CONFIGS

logger = logging.getLogger(__name__)

_ROLE_ORDER = {
    AgentRoutingRole.PERCUSSION: 0,
    AgentRoutingRole.BASS: 1,
    AgentRoutingRole.HARMONY: 2,
    AgentRoutingRole.MELODY: 3,
    AgentRoutingRole.FX: 4,
}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _parse_time_signature(raw: Any) -> Tuple[int, int]:
    text = str(raw or "").strip()
    if "/" not in text:
        return (4, 4)
    try:
        n, d = text.split("/", 1)
        return (max(1, int(n)), max(1, int(d)))
    except Exception:
        return (4, 4)


def _bar_ticks_from_time_signature(time_signature: str) -> int:
    n, d = _parse_time_signature(time_signature)
    return int(TPB * n * (4 / d))


def _section_start_end_bars(
    section: Any,
    blueprint: SongBlueprint,
    section_runtime_index: int,
) -> Tuple[int, int, int]:
    length = max(1, _safe_int(getattr(section, "length_bars", 1), 1))
    start_bar = _safe_int(getattr(section, "start_bar", 0), 0)
    end_bar = _safe_int(getattr(section, "end_bar", 0), 0)
    if start_bar > 0 and end_bar >= start_bar:
        return (start_bar, end_bar, max(1, end_bar - start_bar + 1))

    cursor = 1
    for idx, sec in enumerate(list(getattr(blueprint, "sections", []) or [])):
        sec_len = max(1, _safe_int(getattr(sec, "length_bars", 1), 1))
        if idx == int(section_runtime_index):
            return (cursor, cursor + sec_len - 1, sec_len)
        cursor += sec_len

    return (1, length, length)


def _concept_like(blueprint: SongBlueprint, concept: Optional[SongConcept]) -> Optional[SongConcept]:
    if concept is not None:
        return concept
    candidate = getattr(blueprint, "concept", None)
    if isinstance(candidate, SongConcept):
        return candidate
    return None


def _track_alias(role: AgentRoutingRole) -> str:
    if role == AgentRoutingRole.PERCUSSION:
        return "drums"
    return str(role.value)


def _build_transition_hint(
    blueprint: SongBlueprint,
    section_runtime_index: int,
) -> str:
    """从蓝图中提取相邻 section 的过渡上下文，零额外 LLM 调用。

    返回格式示例:
      [TRANSITION] prev:Intro(E2,last_chord:Am) → current:Verse(E3) → next:Chorus(E4,first_chord:C)
    """
    sections = list(getattr(blueprint, "sections", []) or [])
    if not sections:
        return ""

    idx = int(section_runtime_index)
    if idx < 0 or idx >= len(sections):
        return ""

    cur = sections[idx]
    cur_name = str(getattr(cur, "name", "") or "")
    cur_energy = _safe_int(getattr(cur, "energy_level", 3), 3)

    parts: list[str] = []

    # 前一个 section 的尾部信息
    if idx > 0:
        prev = sections[idx - 1]
        prev_name = str(getattr(prev, "name", "") or "")
        prev_energy = _safe_int(getattr(prev, "energy_level", 3), 3)
        prev_chords = list(getattr(prev, "chord_progression", []) or [])
        last_chord = prev_chords[-1] if prev_chords else "?"
        energy_delta = cur_energy - prev_energy
        delta_label = f"+{energy_delta}" if energy_delta > 0 else str(energy_delta)
        parts.append(f"prev:{prev_name}(E{prev_energy},last_chord:{last_chord},delta:{delta_label})")
    else:
        parts.append("prev:NONE(song_start)")

    parts.append(f"current:{cur_name}(E{cur_energy})")

    # 后一个 section 的头部信息
    if idx < len(sections) - 1:
        nxt = sections[idx + 1]
        nxt_name = str(getattr(nxt, "name", "") or "")
        nxt_energy = _safe_int(getattr(nxt, "energy_level", 3), 3)
        nxt_chords = list(getattr(nxt, "chord_progression", []) or [])
        first_chord = nxt_chords[0] if nxt_chords else "?"
        parts.append(f"next:{nxt_name}(E{nxt_energy},first_chord:{first_chord})")
    else:
        parts.append("next:NONE(song_end)")

    return "[TRANSITION] " + " → ".join(parts)


def dispatch_section_to_requests(
    *,
    section: Any,
    blueprint: SongBlueprint,
    section_runtime_index: int,
    strictness: int = 1,
    concept: Optional[SongConcept] = None,
) -> List[TrackGenRequest]:
    """Build all role requests for one section."""
    concept_obj = _concept_like(blueprint, concept)

    bpm = _safe_int(getattr(concept_obj, "bpm", 120), 120)
    key_root = str(getattr(concept_obj, "key_root", "C") or "C")
    key_mode = str(getattr(concept_obj, "key_mode", "Minor") or "Minor")
    time_signature = str(getattr(concept_obj, "time_signature", "4/4") or "4/4")
    style_description = str(getattr(concept_obj, "style_description", "") or "")
    mood = str(getattr(concept_obj, "mood", "") or "")

    start_bar, end_bar, length = _section_start_end_bars(section, blueprint, section_runtime_index)
    bar_ticks = _bar_ticks_from_time_signature(time_signature)
    start_tick = (start_bar - 1) * bar_ticks
    end_tick = start_tick + (length * bar_ticks)

    section_name = str(getattr(section, "name", f"Section-{section_runtime_index}") or f"Section-{section_runtime_index}")
    declared_section_index = _safe_int(getattr(section, "index", section_runtime_index), section_runtime_index)
    # Use runtime index as execution identity to avoid cross-section collisions on reorder/regenerate.
    section_index = int(section_runtime_index)
    section_energy = max(1, min(5, _safe_int(getattr(section, "energy_level", 3), 3)))
    section_chords = list(getattr(section, "chord_progression", []) or [])
    chord_rhythm = str(getattr(section, "chord_rhythm", "4bar") or "4bar")

    style_text = style_description
    if mood:
        style_text = f"{style_text} | Mood: {mood}" if style_text else f"Mood: {mood}"

    global_anchor_summary = f"[GLOBAL_ANCHOR] {bpm}BPM {key_root}{key_mode} {time_signature}"
    section_summary = f"[SECTION] {section_name} {start_bar}-{end_bar} bars | Energy:{section_energy}/5"

    transition_hint = _build_transition_hint(blueprint, section_runtime_index)

    rows = sorted(
        list(TRACK_DISPATCH_CONFIGS.items()),
        key=lambda item: (int(item[1].compute_layer), int(_ROLE_ORDER.get(item[0], 99))),
    )

    out: List[TrackGenRequest] = []
    for role, config in rows:
        chords = list(section_chords) if bool(getattr(config, "include_chords", True)) else []
        energy_level = float(section_energy - 1) / 4.0
        alias = _track_alias(role)
        # Include runtime section index to avoid cross-section overwrite.
        track_key = f"s{int(section_runtime_index)}_{alias}_main"

        out.append(
            TrackGenRequest(
                instrument=role,
                role=str(role.value),
                track_key=track_key,
                compute_layer=int(config.compute_layer),
                midi_channel=0,
                section_index=section_index,
                section_name=section_name,
                start_bar=start_bar,
                end_bar=end_bar,
                start_tick=start_tick,
                end_tick=end_tick,
                bar_ticks=bar_ticks,
                time_signature=time_signature,
                bpm=bpm,
                key=key_root,
                key_root=key_root,
                scale=key_mode,
                key_mode=key_mode,
                chords=chords,
                chord_progression=chords,
                chord_rhythm=chord_rhythm,
                energy=section_energy,
                energy_level=energy_level,
                style_context=style_text,
                style_description=style_text,
                global_anchor_summary=global_anchor_summary,
                section_summary=section_summary,
                context_summary=transition_hint,
                strictness=int(strictness),
                extra_meta={"declared_section_index": declared_section_index},
            )
        )

    return out


class TrackDispatcher:
    """Compatibility wrapper class over module-level dispatch functions."""

    def dispatch_section_to_requests(
        self,
        section: Any,
        blueprint: SongBlueprint,
        section_runtime_index: int,
        strictness: int = 1,
        concept: Optional[SongConcept] = None,
    ) -> List[TrackGenRequest]:
        return dispatch_section_to_requests(
            section=section,
            blueprint=blueprint,
            section_runtime_index=section_runtime_index,
            strictness=strictness,
            concept=concept,
        )

    def dispatch_blueprint(
        self,
        blueprint: SongBlueprint,
        concept: Optional[SongConcept] = None,
        strictness: int = 1,
    ) -> List[TrackGenRequest]:
        out: List[TrackGenRequest] = []
        for idx, section in enumerate(list(getattr(blueprint, "sections", []) or [])):
            out.extend(
                dispatch_section_to_requests(
                    section=section,
                    blueprint=blueprint,
                    section_runtime_index=idx,
                    strictness=int(strictness),
                    concept=concept,
                )
            )
        logger.info("Dispatched %s requests from blueprint.", len(out))
        return out
