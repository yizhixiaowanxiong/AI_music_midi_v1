from typing import Any, Optional

from schema.arrangement import TrackContext, TrackGenRequest
from schema.blueprint_schema import DetailedSection, SongBlueprint
from schema.section_schema import Strictness
from utils.constants import TPB


class BaseTrackHandler:
    """轨道请求构建基类。"""

    # 请求执行层级：0=无依赖，1=有依赖
    compute_layer: int = 0
    # 是否向下游传递和弦字段
    include_chords: bool = True

    def create_request(
        self,
        section: DetailedSection,
        track_key: str,
        blueprint: SongBlueprint,
        design: Any,
        strictness: Strictness = 1,
        section_runtime_index: Optional[int] = None,
    ) -> TrackGenRequest:
        time_signature = getattr(section, "time_signature", None) or blueprint.concept.time_signature
        bar_ticks = self._calculate_bar_ticks(time_signature)
        section_energy = self._get_section_energy(section, blueprint, section_runtime_index)
        runtime_index = int(section_runtime_index) if section_runtime_index is not None else int(section.index)

        chords = section.chord_progression if self.include_chords else []
        rhythm = section.chord_rhythm if self.include_chords else None

        return TrackGenRequest(
            track_key=track_key,
            compute_layer=self.compute_layer,
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
            root_note=blueprint.concept.scale.root if blueprint.concept.scale else None,
            scale=blueprint.concept.scale,
            global_groove=blueprint.concept.global_groove,
            design=design,
            energy_level=section_energy,
            strictness=strictness,
            # 统一由 utils.context_tools.inject_context_if_needed 在运行时注入。
            context=TrackContext(),
        )

    def _calculate_bar_ticks(self, time_signature: str) -> int:
        try:
            numer, denom = map(int, time_signature.split("/"))
            return int(TPB * numer * (4 / denom))
        except Exception as exc:
            raise ValueError(
                f"Invalid time_signature format '{time_signature}'. Expected 'N/D' (e.g. '4/4')."
            ) from exc

    def _get_section_energy(
        self,
        section: DetailedSection,
        blueprint: SongBlueprint,
        section_runtime_index: Optional[int] = None,
    ) -> float:
        flow = blueprint.concept.structure_flow
        idx = int(section_runtime_index) if section_runtime_index is not None else int(section.index)

        if 0 <= idx < len(flow):
            return flow[idx].energy_curve
        if 0 <= idx - 1 < len(flow):
            return flow[idx - 1].energy_curve
        return 0.6
