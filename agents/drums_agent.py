"""Drums track generation agent."""

from __future__ import annotations

import logging
import json
import os
from typing import List, Optional

from pydantic import ValidationError

from agents.musician_llm_agent import MusicianLlmAgent
from schema.arrangement import GeneratedTrack, TrackGenRequest
from schema.base import NoteEvent
from schema.track_section_schema import DrumsSectionOutput
from utils.constants import MIDI_CHANNEL_DRUMS, TPB
from utils.json_tools import to_dict as _to_dict
from utils.notes import to_note_events
from utils.prompt_compact import (
    compact_design,
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


class DrumsAgent(MusicianLlmAgent):
    """Generate drum patterns for one section."""

    def __init__(self):
        super().__init__(log_prefix="drums")

    def _to_note_events(self, out: DrumsSectionOutput) -> List[NoteEvent]:
        return to_note_events(out)

    async def generate(self, req: TrackGenRequest) -> GeneratedTrack:
        design = req.design.model_dump() if hasattr(req.design, "model_dump") else (req.design or {})
        blueprint = {
            "bpm": req.bpm,
            "time_signature": req.time_signature,
            "style_description": req.style_description or "",
        }
        section = {
            "name": req.section_name,
            "start_bar": req.start_bar,
            "end_bar": req.end_bar,
            "global_energy": req.energy_level if req.energy_level is not None else 0.6,
            "time_signature": req.time_signature,
            "bar_ticks": req.bar_ticks,
            "arrangement": {str(req.track_key or "drums"): design},
        }
        ctx = req.context
        avoidance_rule_text = ctx.to_rule_text() if ctx else ""
        global_anchor_summary = str(getattr(req, "global_anchor_summary", "") or "")
        section_summary = str(getattr(req, "section_summary", "") or "")
        context_summary = str(getattr(req, "context_summary", "") or "")
        context_budget = log_context_budget(
            logger=logger,
            agent="drums",
            track_key=str(req.track_key or ""),
            section_name=str(req.section_name or ""),
            global_anchor_summary=global_anchor_summary,
            section_summary=section_summary,
            avoidance_rule_text=avoidance_rule_text,
            context_summary=context_summary,
        )

        drums_out = await self.generate_for_section(
            blueprint=blueprint,
            section=section,
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
            channel=int(req.midi_channel) if req.midi_channel is not None else MIDI_CHANNEL_DRUMS,
            notes=self._to_note_events(drums_out),
            raw_output=drums_out,
            metrics={"context_budget": context_budget},
        )

    async def generate_for_section(
        self,
        blueprint,
        section,
        track_key: str = "drums",
        strictness: int = 1,
        retries: Optional[int] = None,
        global_anchor_summary: str = "",
        section_summary: str = "",
        context_summary: str = "",
        avoidance_rule_text: str = "",
    ) -> DrumsSectionOutput:
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)

        section_len = int(section["end_bar"]) - int(section["start_bar"]) + 1
        arrangement = section.get("arrangement", {}) or {}

        drums_design = arrangement.get("drums")
        if not drums_design and isinstance(arrangement, dict):
            for _, design in arrangement.items():
                if not isinstance(design, dict):
                    continue
                role_name = str(design.get("role", "") or "").strip().lower()
                if role_name in ("drum", "drums", "percussion"):
                    drums_design = design
                    break
        if not drums_design and isinstance(arrangement, dict) and arrangement:
            drums_design = next(iter(arrangement.values()))
        drums_design = drums_design or {}

        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = int(section.get("bar_ticks", 0) or 0)
        if not bar_ticks:
            n, d = str(time_signature).split("/")
            bar_ticks = int(TPB * int(n) * (4 / int(d)))

        system = (
            "You are a professional electronic drums programmer. "
            "Return exactly one valid JSON object. No markdown, no explanations. "
            "Respect strictness (0=creative, 1=balanced, 2=stable). "
            "Write a clear core groove first; fill must be derived from that groove."
        )

        payload = {
            "task": "generate_drums_section",
            "target_track": {
                "track_key": track_key,
                "role": "drum",
                "instrument_design": compact_design(drums_design),
            },
            "strictness": int(strictness),
            "song": {
                "bpm": int(blueprint.get("bpm") or 120),
                "time_signature": str(time_signature),
                "ticks_per_beat": TPB,
                "bar_ticks": int(bar_ticks),
                "style_description": compact_text(blueprint.get("style_description"), max_chars=160),
            },
            "section": {
                "name": compact_text(section.get("name"), max_chars=48),
                "start_bar": int(section["start_bar"]),
                "end_bar": int(section["end_bar"]),
                "len_bars": int(section_len),
                "global_energy": float(section.get("global_energy", 0.6)),
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
            budget_tokens = get_section_max_tokens("drums", section_len, i)
            try:
                data = await self._call_json(
                    system,
                    prompt,
                    max_tokens=budget_tokens,
                    temperature=0.2 if i == 0 else 0.12,
                    response_model=DrumsSectionOutput,
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
                return DrumsSectionOutput(**data)
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
