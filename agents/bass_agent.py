# bass_agent.py
"""Bass Agent：负责低音轨生成与鼓点上下文对齐。"""

import json
from pydantic import ValidationError
from typing import List

from agents.musician_llm_agent import MusicianLlmAgent
from schema.arrangement import GeneratedTrack, TrackGenRequest
from schema.base import NoteEvent
from schema.bass_schema import BassSectionOutput
from utils.constants import MIDI_CHANNEL_BASS, TPB
from utils.json_tools import to_dict as _to_dict
from utils.notes import to_note_events
from utils.token_budget import get_section_max_tokens

import copy


class BassAgent(MusicianLlmAgent):
    """低音生成 Agent。"""

    def __init__(self):
        super().__init__(log_prefix="bass")

    def _to_note_events(self, out: BassSectionOutput) -> List[NoteEvent]:
        return to_note_events(out)

    async def generate(self, req: TrackGenRequest) -> GeneratedTrack:
        """将调度请求转换为 bass 段落生成，并返回标准轨道结果。"""
        scale_name = None
        if getattr(req, "scale", None) is not None:
            scale_name = getattr(req.scale, "name", None) or str(req.scale)

        blueprint = {
            "bpm": req.bpm,
            "root_note": req.root_note or "",
            "scale": scale_name or "",
        }

        section = {
            "name": req.section_name,
            "start_bar": req.start_bar,
            "end_bar": req.end_bar,
            "len_bars": req.end_bar - req.start_bar + 1,
            "global_energy": req.energy_level if req.energy_level is not None else 0.7,
            "chord_progression": req.chord_progression or [],
            "chord_rhythm": req.chord_rhythm or "4bar",
            "time_signature": req.time_signature,
        }

        design = {}
        if getattr(req, "design", None) is not None:
            if hasattr(req.design, "model_dump"):
                design = req.design.model_dump()
            else:
                design = req.design

        ctx = req.context
        # 来自 drums 的 runtime context，用于避开 kick 强拍冲突。
        drums_summary = {
            "kick_onsets_ticks": getattr(ctx, "kick_onsets_ticks", []) if ctx else [],
            "llm_context_text": getattr(ctx, "kick_summary_text", "") if ctx else "",
        }


        bass_out = await self.generate_bass_for_section(
            blueprint,
            section,
            design,
            drums_summary,
            track_key=req.track_key,
            strictness=getattr(req, "strictness", 1),
        )
        return GeneratedTrack(
            track_key=req.track_key,
            instrument=req.instrument,
            section_name=req.section_name,
            channel=int(req.midi_channel) if req.midi_channel is not None else MIDI_CHANNEL_BASS,
            notes=self._to_note_events(bass_out),
            raw_output=bass_out,
        )

    async def generate_bass_for_section(
        self,
        blueprint: dict,
        section: dict,
        design: dict,
        drums_summary: dict,
        track_key: str = "bass",
        strictness: int = 1,
        retries: int = 2,
    ) -> 'BassSectionOutput':
        """生成单段 bass 结构化输出（pattern + phrase）。"""
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)
        design = _to_dict(design)
        section_len = section["end_bar"] - section["start_bar"] + 1
        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = int(section.get("bar_ticks", 0) or 0)
        if not bar_ticks:
            n, d = time_signature.split("/")
            bar_ticks = int(TPB * int(n) * (4 / int(d)))

        system = (
            "You are a Deep House / Tech House bassline writer.\n"
            "Return JSON only.\n"
            "Rules:\n"
            "- pattern_len_bars must be one of 1,2,4,8.\n"
            "- note.start_tick must be inside one pattern cycle.\n"
            "- phrases must fully cover section bars without overlap.\n"
            "- phrase tags must exist in patterns.\n"
            "- Follow chord progression; prefer root/5th/3rd on strong beats.\n"
            "- Use Kick Summary to avoid strong bass attacks exactly on kick steps.\n"
            "- strictness 0/1/2 means creative/balanced/stable.\n"
        )

        # 给 LLM 一个清晰的“长什么样”
        example = {
            "track_key": track_key,
            "section_name": section["name"],
            "section_start_bar": section["start_bar"],
            "section_end_bar": section["end_bar"],
            "time_signature": time_signature,
            "ticks_per_beat": TPB,
            "bar_ticks": bar_ticks,
            "strictness": 1,
            "pattern_len_bars": 1,
            "patterns": [
                {
                    "tag": "core",
                    "notes": [
                        {"pitch": 36, "start_tick": 240, "duration_tick": 120, "velocity": 105},
                    ],
                }
            ],
            "phrases": [
                {"start_bar": section["start_bar"], "end_bar": section["end_bar"], "use_pattern_tag": "core"}
            ],
        }

        user = {
            "target_track": {
                "track_key": track_key,
                "role": "bass",
                "instrument_design": design,
            },
            "blueprint_global": {
                "bpm": blueprint["bpm"],
                "root_note": blueprint["root_note"],
                "scale": blueprint["scale"],
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "len_bars": section_len,
                "global_energy": section.get("global_energy", 0.7),
                "chord_progression": section.get("chord_progression", []),
                "chord_rhythm": section.get("chord_rhythm", "4bar"),
            },
            "drums_kick_summary_for_listening": drums_summary.get("llm_context_text", ""),
            "strictness": strictness,
            "output_shape_example": example,
        }

        prompt = json.dumps(user, ensure_ascii=False)

        last_err = None
        for i in range(retries + 1):
            budget_tokens = get_section_max_tokens("bass", section_len, i)
            try:
                data = await self._call_json(
                    system,
                    prompt,
                    max_tokens=budget_tokens,
                    temperature=0.25 if i == 0 else 0.15,
                )
            except json.JSONDecodeError as e:
                # JSON 语法异常时，给模型显式修复指令并重试。
                last_err = e
                fix_payload = copy.deepcopy(user)
                fix_payload["fix_instruction"] = (
                    "Your previous output was not valid JSON. "
                    "Return ONLY a single valid JSON object. "
                    "Use double quotes, include all required commas, and no extra text."
                )
                fix_payload["previous_json_error"] = str(e)
                prompt = json.dumps(fix_payload, ensure_ascii=False)
                continue
            try:
                return BassSectionOutput(**data)
            except ValidationError as e:
                # Schema 不匹配时，保留原上下文并要求仅修正非法字段。
                last_err = e
                fix_payload = copy.deepcopy(user)
                fix_payload["fix_instruction"] = "Fix ONLY invalid/missing fields. Keep all valid fields unchanged. Return FULL corrected JSON only."
                fix_payload["previous_validation_error"] = str(e)[:600]
                prompt = json.dumps(fix_payload, ensure_ascii=False)

        raise RuntimeError(f"Validation failed after retries: {last_err}")
