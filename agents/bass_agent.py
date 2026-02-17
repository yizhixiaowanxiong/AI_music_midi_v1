"""Bass track generation agent."""

from __future__ import annotations

import logging
import json
import os
from typing import List, Optional

from pydantic import ValidationError

from agents.musician_llm_agent import MusicianLlmAgent
from schema.arrangement import GeneratedTrack, TrackGenRequest
from schema.base import NoteEvent
from schema.track_section_schema import BassSectionOutput
from utils.constants import MIDI_CHANNEL_BASS, TPB
from utils.json_tools import to_dict as _to_dict
from utils.notes import to_note_events
from utils.prompt_compact import (
    compact_chords,
    compact_design,
    compact_scale_name,
    compact_text,
    section_output_contract,
)
from utils.context_budget import log_context_budget
from utils.token_budget import get_section_max_tokens


def _resolve_schema_retries(retries: Optional[int]) -> int:
    if retries is not None:
        return max(0, int(retries))
    raw = str(os.getenv("MUSICIAN_SCHEMA_RETRIES", "")).strip()
    if not raw:
        return 1
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


logger = logging.getLogger(__name__)


class BassAgent(MusicianLlmAgent):
    """Generate bass patterns for one section."""

    def __init__(self):
        super().__init__(log_prefix="bass")

    def _to_note_events(self, out: BassSectionOutput) -> List[NoteEvent]:
        return to_note_events(out)

    async def generate(self, req: TrackGenRequest) -> GeneratedTrack:
        scale_name = compact_scale_name(None, req.scale)

        blueprint = {
            "bpm": req.bpm,
            "scale_name": scale_name,
            "time_signature": req.time_signature,
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
            "bar_ticks": req.bar_ticks,
        }
        design = req.design.model_dump() if hasattr(req.design, "model_dump") else (req.design or {})

        ctx = req.context
        avoidance_rule_text = ctx.to_rule_text() if ctx else ""
        global_anchor_summary = str(getattr(req, "global_anchor_summary", "") or "")
        section_summary = str(getattr(req, "section_summary", "") or "")
        context_summary = str(getattr(req, "context_summary", "") or "")
        context_budget = log_context_budget(
            logger=logger,
            agent="bass",
            track_key=str(req.track_key or ""),
            section_name=str(req.section_name or ""),
            global_anchor_summary=global_anchor_summary,
            section_summary=section_summary,
            avoidance_rule_text=avoidance_rule_text,
            context_summary=context_summary,
        )

        bass_out = await self.generate_bass_for_section(
            blueprint=blueprint,
            section=section,
            design=design,
            track_key=req.track_key,
            strictness=getattr(req, "strictness", 1),
            global_anchor_summary=global_anchor_summary,
            section_summary=section_summary,
            context_summary=context_summary,
            avoidance_rule_text=avoidance_rule_text,
        )
        return GeneratedTrack(
            track_key=req.track_key,
            instrument=req.instrument,
            section_name=req.section_name,
            channel=int(req.midi_channel) if req.midi_channel is not None else MIDI_CHANNEL_BASS,
            notes=self._to_note_events(bass_out),
            raw_output=bass_out,
            metrics={"context_budget": context_budget},
        )

    async def generate_bass_for_section(
        self,
        blueprint: dict,
        section: dict,
        design: dict,
        drums_summary: Optional[dict] = None,
        track_key: str = "bass",
        strictness: int = 1,
        retries: Optional[int] = None,
        global_anchor_summary: str = "",
        section_summary: str = "",
        context_summary: str = "",
        avoidance_rule_text: str = "",
    ) -> BassSectionOutput:
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)
        design = _to_dict(design)

        section_len = int(section["end_bar"]) - int(section["start_bar"]) + 1
        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = int(section.get("bar_ticks", 0) or 0)
        if not bar_ticks:
            n, d = str(time_signature).split("/")
            bar_ticks = int(TPB * int(n) * (4 / int(d)))

        system = (
            "You are a deep-house bass writer. "
            "Return exactly one valid JSON object. No markdown, no explanations. "
            "Respect strictness (0=creative, 1=balanced, 2=stable). "
            "Follow section bars/chords and avoid exact collisions with kick accents."
        )

        payload = {
            "task": "generate_bass_section",
            "target_track": {
                "track_key": track_key,
                "role": "bass",
                "instrument_design": compact_design(design),
            },
            "strictness": int(strictness),
            "song": {
                "bpm": int(blueprint.get("bpm") or 120),
                "time_signature": str(time_signature),
                "ticks_per_beat": TPB,
                "bar_ticks": int(bar_ticks),
                "scale_name": compact_text(blueprint.get("scale_name"), max_chars=48),
            },
            "section": {
                "name": compact_text(section.get("name"), max_chars=48),
                "start_bar": int(section["start_bar"]),
                "end_bar": int(section["end_bar"]),
                "len_bars": int(section_len),
                "global_energy": float(section.get("global_energy", 0.7)),
                "chord_progression": compact_chords(section.get("chord_progression"), max_items=8),
                "chord_rhythm": compact_text(section.get("chord_rhythm"), max_chars=12) or "4bar",
            },
            "output_contract": section_output_contract(track_key),
        }
        payload = self._inject_context_layer_payload(
            payload,
            global_anchor_summary=global_anchor_summary,
            section_summary=section_summary,
            avoidance_rule_text=avoidance_rule_text,
            context_summary=context_summary,
        )

        prompt = json.dumps(payload, ensure_ascii=False)
        attempts = _resolve_schema_retries(retries)
        last_err: Exception | None = None

        for i in range(attempts + 1):
            budget_tokens = get_section_max_tokens("bass", section_len, i)
            try:
                data = await self._call_json(
                    system,
                    prompt,
                    max_tokens=budget_tokens,
                    temperature=0.2 if i == 0 else 0.12,
                    response_model=BassSectionOutput,
                )
            except json.JSONDecodeError as exc:
                last_err = exc
                retry_payload = dict(payload)
                retry_payload["retry_hint"] = (
                    "Previous output was invalid JSON. Return one complete JSON object only."
                )
                retry_payload["previous_json_error"] = str(exc)
                prompt = json.dumps(retry_payload, ensure_ascii=False)
                continue

            try:
                return BassSectionOutput(**data)
            except ValidationError as exc:
                last_err = exc
                retry_payload = dict(payload)
                retry_payload["retry_hint"] = (
                    "Previous output failed schema validation. Keep valid fields, fix invalid ones, "
                    "and return full corrected JSON."
                )
                retry_payload["previous_validation_error"] = str(exc)[:500]
                prompt = json.dumps(retry_payload, ensure_ascii=False)

        raise RuntimeError(f"Validation failed after retries: {last_err}")

