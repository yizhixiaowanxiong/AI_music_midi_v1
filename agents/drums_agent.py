# drums_agent.py
from __future__ import annotations

"""Drums Agent：负责鼓轨生成与后置稳定化规则。"""

import copy
import json
from typing import List

from pydantic import ValidationError

from agents.musician_llm_agent import MusicianLlmAgent
from schema.arrangement import GeneratedTrack, TrackGenRequest
from schema.base import NoteEvent
from schema.drum_schema import DrumsSectionOutput
from utils.constants import MIDI_CHANNEL_DRUMS, TPB
from utils.json_tools import to_dict as _to_dict
from utils.notes import to_note_events
from utils.token_budget import get_section_max_tokens

def _calc_bar_ticks(time_signature: str, tpb: int) -> int:
    """按拍号与 TPB 计算每小节 tick。"""
    n, d = time_signature.split("/")
    return int(tpb * int(n) * (4 / int(d)))


class DrumsAgent(MusicianLlmAgent):
    """鼓组生成 Agent。"""

    def __init__(self):
        super().__init__(log_prefix="drums")

    def _to_note_events(self, out: DrumsSectionOutput) -> List[NoteEvent]:
        return to_note_events(out)

    async def generate(self, req: TrackGenRequest) -> GeneratedTrack:
        """把统一调度请求转为鼓组段落生成，再回填为标准轨道输出。"""
        design = getattr(req, "design", None)
        design_payload = design.model_dump() if design is not None and hasattr(design, "model_dump") else (design or {})

        blueprint = {
            "bpm": req.bpm,
            "time_signature": req.time_signature,
            "style_description": req.style_description or "",
            "groove_global": req.global_groove.model_dump() if req.global_groove else {},
        }
        section = {
            "name": req.section_name,
            "start_bar": req.start_bar,
            "end_bar": req.end_bar,
            "global_energy": req.energy_level if req.energy_level is not None else 0.6,
            "time_signature": req.time_signature,
            "arrangement": {str(req.track_key or "drums"): design_payload},
        }

        drums_out = await self.generate_for_section(
            blueprint=blueprint,
            section=section,
            track_key=req.track_key,
            strictness=getattr(req, "strictness", 1),
        )
        return GeneratedTrack(
            track_key=req.track_key,
            instrument=req.instrument,
            section_name=req.section_name,
            channel=int(req.midi_channel) if req.midi_channel is not None else MIDI_CHANNEL_DRUMS,
            notes=self._to_note_events(drums_out),
            raw_output=drums_out,
        )

    async def generate_for_section(
        self,
        blueprint,
        section,
        track_key: str = "drums",
        strictness: int = 1,
        retries: int = 2,
    ) -> DrumsSectionOutput:
        """为单段落生成鼓组 pattern/phrase 输出。"""
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)
        section_len = section["end_bar"] - section["start_bar"] + 1

        arrangement = section.get("arrangement", {}) or {}
        # 兼容任意 track_key 的鼓轨写法，不强依赖固定 key=drums。
        drums_inst = arrangement.get("drums")
        if not drums_inst and isinstance(arrangement, dict):
            for _, design in arrangement.items():
                if not isinstance(design, dict):
                    continue
                role_name = str(design.get("role", "") or "").strip().lower()
                if role_name in ("percussion", "drums"):
                    drums_inst = design
                    break
        if not drums_inst and isinstance(arrangement, dict) and arrangement:
            drums_inst = next(iter(arrangement.values()))
        drums_inst = drums_inst or {}

        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = _calc_bar_ticks(time_signature, TPB)

        system = (
            "You are a professional drum programmer.\n"
            "Return JSON only.\n"
            f"time_signature={time_signature}, ticks_per_beat={TPB}, bar_ticks={bar_ticks}.\n"
            "Rules:\n"
            "- pattern_len_bars must be one of 1,2,4,8.\n"
            "- note.start_tick must be inside one pattern cycle.\n"
            "- phrases must fully cover section bars without overlap.\n"
            "- phrase tags must exist in patterns.\n"
            "- Use GM core mapping: kick36 snare38 clap39 chh42 ohh46 ride51.\n"
            "- Create core groove first; fill should be a variation of core, not from scratch.\n"
            "- strictness 0/1/2 means creative/balanced/stable.\n"
        )

        example = {
            "track_key": track_key,
            "section_name": section["name"],
            "section_start_bar": section["start_bar"],
            "section_end_bar": section["end_bar"],
            "time_signature": time_signature,
            "pattern_len_bars": 1,
            "ticks_per_beat": TPB,
            "bar_ticks": bar_ticks,
            "patterns": [
                {"tag": "core", "notes": [{"pitch": 36, "start_tick": 0, "duration_tick": 60, "velocity": 110}]},
                {
                    "tag": "fill",
                    "notes": [{"pitch": 38, "start_tick": max(0, bar_ticks - 240), "duration_tick": 60, "velocity": 100}],
                },
            ],
            "phrases": [
                {
                    "start_bar": section["start_bar"],
                    "end_bar": section["end_bar"],
                    "use_pattern_tag": "core",
                    "end_fill_tag": "fill",
                }
            ],
        }

        user_payload = {
            "target_track": {
                "track_key": track_key,
                "role": "percussion",
                "instrument_design": drums_inst,
            },
            "strictness": strictness,
            "song": {
                "bpm": blueprint.get("bpm"),
                "time_signature": time_signature,
                "ticks_per_beat": TPB,
                "bar_ticks": bar_ticks,
                "groove_global": blueprint.get("groove_global", {}),
                "style_description": blueprint.get("style_description", ""),
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "len_bars": section_len,
                "global_energy": section.get("global_energy", 0.5),
                "drums_instruction": drums_inst,
            },
            "example_shape": example,
        }

        prompt = json.dumps(user_payload, ensure_ascii=False)
        last_err = None
        for i in range(retries + 1):
            budget_tokens = get_section_max_tokens("drums", section_len, i)
            try:
                data = await self._call_json(
                    system,
                    prompt,
                    max_tokens=budget_tokens,
                    temperature=0.25 if i == 0 else 0.15,
                )
            except json.JSONDecodeError as exc:
                # 结构正确但非合法 JSON 时，追加修复指令重试。
                last_err = exc
                fix_payload = copy.deepcopy(user_payload)
                fix_payload["fix_instruction"] = (
                    "Your previous output was not valid JSON. "
                    "Return ONLY a single valid JSON object. "
                    "Use double quotes, include all required commas, and no extra text."
                )
                fix_payload["previous_json_error"] = str(exc)
                prompt = json.dumps(fix_payload, ensure_ascii=False)
                continue

            try:
                output = DrumsSectionOutput(**data)
                return output
            except ValidationError as exc:
                last_err = exc
                fix_payload = copy.deepcopy(user_payload)
                fix_payload["fix_instruction"] = (
                    "Fix ONLY invalid/missing fields. Keep all valid fields unchanged. Return FULL corrected JSON only."
                )
                fix_payload["previous_validation_error"] = str(exc)[:600]
                prompt = json.dumps(fix_payload, ensure_ascii=False)

        raise RuntimeError(f"Validation failed after retries: {last_err}")
