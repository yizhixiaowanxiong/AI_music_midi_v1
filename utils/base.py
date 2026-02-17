from __future__ import annotations

import logging
from typing import Any, Optional

from schema.arrangement import TrackContext, TrackGenRequest
from schema.blueprint_schema import DetailedSection, SongBlueprint
from schema.section_schema import Strictness
from utils.constants import TPB
from utils.context_summary import (
    build_global_anchor_summary,
    build_section_summary,
)


class BaseTrackHandler:
    """Base request builder for section-track generation."""

    compute_layer: int = 0
    include_chords: bool = True

    def create_request(
        self,
        section: DetailedSection,
        track_key: str,
        blueprint: SongBlueprint,
        design: Any,
        strictness: Strictness = 1,
        section_runtime_index: Optional[int] = None,
        *,
        compute_layer: Optional[int] = None,
        include_chords: Optional[bool] = None,
    ) -> TrackGenRequest:
        time_signature = getattr(section, "time_signature", None) or blueprint.concept.time_signature
        bar_ticks = self._calculate_bar_ticks(time_signature)
        section_energy = self._get_section_energy(section, blueprint, section_runtime_index)
        section_energy_level = getattr(section, "energy_level", None)
        if section_energy_level is not None:
            try:
                section_energy_level = max(1, min(5, int(section_energy_level)))
                section_energy = float((section_energy_level - 1) / 4.0)
            except Exception:
                section_energy_level = None
        section_function = str(getattr(section, "section_function", "") or "").strip()
        runtime_index = int(section_runtime_index) if section_runtime_index is not None else int(section.index)
        effective_compute_layer = int(self.compute_layer if compute_layer is None else compute_layer)
        effective_include_chords = bool(self.include_chords if include_chords is None else include_chords)

        chords = section.chord_progression if effective_include_chords else []
        rhythm = section.chord_rhythm if effective_include_chords else None

        global_anchor_summary = build_global_anchor_summary(
            concept=blueprint.concept,
            total_bars=int(blueprint.total_bars),
            chord_progression=chords,
        )
        section_summary = build_section_summary(
            section_name=section.name,
            start_bar=int(section.start_bar),
            end_bar=int(section.end_bar),
            section_energy=float(section_energy),
            arrangement_size=len(section.arrangement or {}),
            transition_to_next=str(getattr(section, "transition_to_next", "") or ""),
            section_energy_level=section_energy_level,
            section_function=section_function or None,
        )
        return TrackGenRequest(
            track_key=track_key,
            compute_layer=max(0, effective_compute_layer),
            section_index=runtime_index,
            section_name=section.name,
            instrument=design.role,
            midi_channel=None,
            bpm=blueprint.concept.bpm,
            time_signature=time_signature,
            ticks_per_beat=TPB,
            bar_ticks=bar_ticks,
            start_bar=section.start_bar,
            end_bar=section.end_bar,
            chord_progression=chords,
            chord_rhythm=rhythm,
            style_description=blueprint.concept.style_description,
            scale=blueprint.concept.scale,
            design=design,
            energy_level=section_energy,
            strictness=strictness,
            context=TrackContext(),
            global_anchor_summary=global_anchor_summary,
            section_summary=section_summary,
            context_summary=None,
        )

    def _calculate_bar_ticks(self, time_signature: str) -> int:
        try:
            numer, denom = map(int, str(time_signature).split("/"))
            if numer <= 0 or denom <= 0:
                raise ValueError("numerator/denominator must be > 0")
            return max(1, int(TPB * numer * (4 / denom)))
        except Exception:
            # Best-effort: malformed time signature falls back to 4/4.
            logging.getLogger(__name__).warning(
                "invalid_time_signature_fallback_4_4 value=%r",
                time_signature,
            )
            return int(TPB * 4)

    def _get_section_energy(
        self,
        section: DetailedSection,
        blueprint: SongBlueprint,
        section_runtime_index: Optional[int] = None,
    ) -> float:
        flow = list(blueprint.concept.structure_flow or [])
        idx = int(section_runtime_index) if section_runtime_index is not None else int(section.index)
        if 0 <= idx < len(flow):
            return float(flow[idx].energy_curve)
        if 0 <= idx - 1 < len(flow):
            return float(flow[idx - 1].energy_curve)
        return 0.6
