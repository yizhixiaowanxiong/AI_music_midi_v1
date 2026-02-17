import logging
from typing import Dict, List

from schema.arrangement import TrackContext, TrackGenRequest
from schema.base import AgentRoutingRole
from schema.blueprint_schema import DetailedSection, SongBlueprint
from schema.section_schema import Strictness
from utils.base import BaseTrackHandler
from utils.constants import TPB
from utils.context_summary import build_global_anchor_summary, build_section_summary
from utils.track_dispatch_config import (
    DEFAULT_TRACK_DISPATCH_CONFIG,
    TRACK_DISPATCH_CONFIGS,
)

logger = logging.getLogger(__name__)


class TrackDispatcher:
    def __init__(self):
        self._request_builder = BaseTrackHandler()
        self._configs = dict(TRACK_DISPATCH_CONFIGS)
        self._default_config = DEFAULT_TRACK_DISPATCH_CONFIG

    def dispatch_section(
        self,
        section: DetailedSection,
        blueprint: SongBlueprint,
        section_runtime_index: int,
        strictness: Strictness = 1,
    ) -> List[TrackGenRequest]:
        """Build section track requests (no routing/cap decision here)."""
        requests: List[TrackGenRequest] = []
        for track_key, design in section.arrangement.items():
            config = self._configs.get(getattr(design, "role", None), self._default_config)
            try:
                req = self._request_builder.create_request(
                    section=section,
                    track_key=track_key,
                    blueprint=blueprint,
                    design=design,
                    strictness=strictness,
                    section_runtime_index=section_runtime_index,
                    compute_layer=int(config.compute_layer),
                    include_chords=bool(config.include_chords),
                )
            except Exception as exc:
                logger.warning(
                    "dispatch_best_effort_fallback section=%s track=%s role=%s error=%s",
                    str(getattr(section, "name", "") or ""),
                    str(track_key or ""),
                    str(getattr(getattr(design, "role", None), "value", getattr(design, "role", None)) or ""),
                    str(exc),
                )
                req = _build_best_effort_request(
                    section=section,
                    blueprint=blueprint,
                    design=design,
                    track_key=track_key,
                    strictness=strictness,
                    section_runtime_index=section_runtime_index,
                    compute_layer=int(config.compute_layer),
                    include_chords=bool(config.include_chords),
                )
            requests.append(req)

        requests.sort(key=lambda r: r.compute_layer)
        return list(requests)


_dispatcher_instance = TrackDispatcher()


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_bars(section: DetailedSection) -> tuple[int, int]:
    start_bar = max(1, _safe_int(getattr(section, "start_bar", 1), 1))
    end_bar = max(start_bar, _safe_int(getattr(section, "end_bar", start_bar), start_bar))
    return start_bar, end_bar


def _safe_time_signature(section: DetailedSection, blueprint: SongBlueprint) -> str:
    value = str(getattr(section, "time_signature", "") or getattr(blueprint.concept, "time_signature", "4/4")).strip()
    try:
        n, d = map(int, value.split("/"))
        if n <= 0 or d <= 0:
            raise ValueError("time signature must be positive")
        return f"{n}/{d}"
    except Exception:
        return "4/4"


def _safe_bar_ticks(time_signature: str) -> int:
    try:
        n, d = map(int, str(time_signature or "4/4").split("/"))
        if n <= 0 or d <= 0:
            raise ValueError("time signature must be positive")
        return max(1, int(TPB * n * (4 / d)))
    except Exception:
        return int(TPB * 4)


def _safe_role(design, track_key: str) -> AgentRoutingRole:
    role = getattr(design, "role", None)
    if isinstance(role, AgentRoutingRole):
        return role
    text = str(track_key or "").lower()
    if "drum" in text or "perc" in text:
        return AgentRoutingRole.PERCUSSION
    if "bass" in text or "sub" in text:
        return AgentRoutingRole.BASS
    if "harm" in text or "pad" in text or "chord" in text:
        return AgentRoutingRole.HARMONY
    if "lead" in text or "melody" in text:
        return AgentRoutingRole.MELODY
    if "fx" in text:
        return AgentRoutingRole.FX
    return AgentRoutingRole.PERCUSSION


def _build_best_effort_request(
    *,
    section: DetailedSection,
    blueprint: SongBlueprint,
    design,
    track_key: str,
    strictness: Strictness,
    section_runtime_index: int,
    compute_layer: int,
    include_chords: bool,
) -> TrackGenRequest:
    start_bar, end_bar = _normalize_bars(section)
    time_signature = _safe_time_signature(section, blueprint)
    bar_ticks = _safe_bar_ticks(time_signature)
    section_name = str(getattr(section, "name", "") or "Section")
    role = _safe_role(design, track_key)

    chords = list(getattr(section, "chord_progression", []) or []) if include_chords else []
    chord_rhythm = getattr(section, "chord_rhythm", None) if include_chords else None
    section_energy = float((_safe_int(getattr(section, "energy_level", 3), 3) - 1) / 4.0)

    try:
        global_anchor_summary = build_global_anchor_summary(
            concept=blueprint.concept,
            total_bars=max(1, _safe_int(getattr(blueprint, "total_bars", end_bar), end_bar)),
            chord_progression=chords,
        )
    except Exception:
        global_anchor_summary = ""

    try:
        section_summary = build_section_summary(
            section_name=section_name,
            start_bar=start_bar,
            end_bar=end_bar,
            section_energy=section_energy,
            arrangement_size=len(getattr(section, "arrangement", {}) or {}),
            transition_to_next=str(getattr(section, "transition_to_next", "") or ""),
            section_energy_level=_safe_int(getattr(section, "energy_level", 3), 3),
            section_function=str(getattr(section, "section_function", "") or "").strip() or None,
        )
    except Exception:
        section_summary = ""

    return TrackGenRequest(
        track_key=str(track_key or "track"),
        compute_layer=max(0, int(compute_layer)),
        section_index=max(0, _safe_int(section_runtime_index, _safe_int(getattr(section, "index", 0), 0))),
        section_name=section_name,
        instrument=role,
        midi_channel=None,
        bpm=max(1, _safe_int(getattr(blueprint.concept, "bpm", 120), 120)),
        time_signature=time_signature,
        ticks_per_beat=TPB,
        bar_ticks=bar_ticks,
        start_bar=start_bar,
        end_bar=end_bar,
        chord_progression=chords,
        chord_rhythm=chord_rhythm,
        style_description=str(getattr(blueprint.concept, "style_description", "") or ""),
        scale=getattr(blueprint.concept, "scale", None),
        design=design if hasattr(design, "role") else None,
        energy_level=section_energy,
        strictness=strictness,
        context=TrackContext(),
        global_anchor_summary=global_anchor_summary,
        section_summary=section_summary,
    )


def dispatch_section_to_requests(
    section: DetailedSection,
    blueprint: SongBlueprint,
    section_runtime_index: int,
    strictness: Strictness = 1,
) -> List[TrackGenRequest]:
    return _dispatcher_instance.dispatch_section(
        section=section,
        blueprint=blueprint,
        section_runtime_index=section_runtime_index,
        strictness=strictness,
    )
