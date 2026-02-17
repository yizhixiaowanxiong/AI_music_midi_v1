"""Director agent for building an executable song blueprint from concept."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.concept_agent import ConceptAgent
from agents.llm_base_agent import BaseAgent
from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept
from utils.duration import default_duration_from_concept


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


class BlueprintDraft(BaseModel):
    """Loose blueprint payload schema for structured output mode."""

    concept: Dict[str, Any] = Field(default_factory=dict)
    user_confirmed_duration_sec: float = 0.0
    total_bars: int = 0
    sections: List[Dict[str, Any]] = Field(default_factory=list)


class DirectorAgent(BaseAgent):
    """Arrangement-level agent that outputs SongBlueprint."""

    def __init__(self):
        super().__init__(system_prompt="You are a professional music director and arranger.")
        # Keep blueprint stage timeout stricter than global defaults to avoid long MCP waits.
        blueprint_timeout_default = _env_float(
            "BLUEPRINT_LLM_TIMEOUT_SEC",
            default=min(self.request_timeout_sec, 45.0),
            min_value=5.0,
            max_value=300.0,
        )
        blueprint_total_timeout_default = max(
            blueprint_timeout_default,
            min(self.total_timeout_sec, 120.0),
        )
        blueprint_total_timeout = _env_float(
            "BLUEPRINT_LLM_TOTAL_TIMEOUT_SEC",
            default=blueprint_total_timeout_default,
            min_value=blueprint_timeout_default,
            max_value=600.0,
        )
        self.request_timeout_sec = float(blueprint_timeout_default)
        self.total_timeout_sec = float(blueprint_total_timeout)
        self._gateway.request_timeout_sec = float(self.request_timeout_sec)
        self._gateway.total_timeout_sec = float(self.total_timeout_sec)

        self._max_tokens = _env_int("BLUEPRINT_MAX_TOKENS", default=1200, min_value=700, max_value=2600)
        soft_cap = _env_int("BLUEPRINT_MAX_TOKENS_SOFT_CAP", default=1200, min_value=0, max_value=3200)
        if soft_cap > 0:
            self._max_tokens = min(self._max_tokens, soft_cap)
        self._retries = _env_int("BLUEPRINT_LLM_RETRIES", default=1, min_value=0, max_value=3)
        self._temperature = _env_float("BLUEPRINT_TEMPERATURE", default=0.2, min_value=0.0, max_value=0.6)

    def _target_max_tokens(self, duration_sec: float) -> int:
        d = float(duration_sec)
        if d <= 40.0:
            return int(min(self._max_tokens, 900))
        if d <= 90.0:
            return int(min(self._max_tokens, 1100))
        return int(self._max_tokens)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _default_arrangement() -> Dict[str, Dict[str, str]]:
        return {
            "drums_main": {
                "role": "drum",
                "instrument_name": "Drum Kit",
                "playing_style": "steady groove",
            },
            "bass_main": {
                "role": "bass",
                "instrument_name": "Electric Bass",
                "playing_style": "root notes",
            },
        }

    def _normalize_arrangement(self, arrangement: Any) -> Dict[str, Dict[str, str]]:
        if not isinstance(arrangement, dict) or not arrangement:
            return self._default_arrangement()

        out: Dict[str, Dict[str, str]] = {}
        valid_roles = {"drum", "drums", "percussion", "bass", "harmony", "melody", "fx"}
        for raw_key, raw_value in arrangement.items():
            key = str(raw_key or "").strip() or f"track_{len(out)}"
            value = dict(raw_value) if isinstance(raw_value, dict) else {}

            role = str(value.get("role") or "").strip().lower()
            if role not in valid_roles:
                k = key.lower()
                if "drum" in k or "perc" in k:
                    role = "drum"
                elif "bass" in k or "sub" in k:
                    role = "bass"
                elif "harm" in k or "pad" in k or "chord" in k:
                    role = "harmony"
                elif "melody" in k or "lead" in k:
                    role = "melody"
                elif "fx" in k:
                    role = "fx"
                else:
                    role = "harmony"
            elif role in {"drums", "percussion"}:
                role = "drum"

            instrument_name = str(value.get("instrument_name") or role).strip() or role
            playing_style = str(value.get("playing_style") or "supportive pattern").strip() or "supportive pattern"

            item: Dict[str, str] = {
                "role": role,
                "instrument_name": instrument_name,
                "playing_style": playing_style,
            }
            mixing_hint = value.get("mixing_hint")
            if mixing_hint is not None and str(mixing_hint).strip():
                item["mixing_hint"] = str(mixing_hint).strip()
            out[key] = item

        if not out:
            return self._default_arrangement()
        return out

    def _normalize_blueprint_payload(
        self,
        *,
        payload: Any,
        concept: SongConcept,
        user_confirmed_duration_sec: float,
    ) -> Dict[str, Any]:
        raw = dict(payload) if isinstance(payload, dict) else {}
        wrapped = raw.get("SongBlueprint")
        if isinstance(wrapped, dict):
            raw = dict(wrapped)

        sections_raw = list(raw.get("sections") or [])
        sections_tmp: List[Dict[str, Any]] = []

        for i, sec in enumerate(sections_raw):
            if not isinstance(sec, dict):
                continue
            name = str(sec.get("name") or f"Section {i + 1}").strip() or f"Section {i + 1}"
            start_bar = max(1, self._safe_int(sec.get("start_bar"), i * 8 + 1))
            end_bar = max(start_bar, self._safe_int(sec.get("end_bar"), start_bar + 7))
            length = max(1, end_bar - start_bar + 1)
            energy_level = max(1, min(5, self._safe_int(sec.get("energy_level"), 3)))
            section_function = str(sec.get("section_function") or "推进").strip() or "推进"

            chords = sec.get("chord_progression")
            if not isinstance(chords, list) or not [x for x in chords if str(x).strip()]:
                chords = ["Cm"]
            else:
                chords = [str(x).strip() for x in chords if str(x).strip()][:8]

            chord_rhythm = str(sec.get("chord_rhythm") or "4bar").strip()
            if chord_rhythm not in {"8bar", "4bar", "2bar", "1bar", "2beats", "1beat"}:
                chord_rhythm = "4bar"

            arrangement = self._normalize_arrangement(sec.get("arrangement"))
            transition_to_next = str(sec.get("transition_to_next") or "smooth transition").strip() or "smooth transition"

            sections_tmp.append(
                {
                    "name": name,
                    "index": i,
                    "start_bar": start_bar,
                    "end_bar": end_bar,
                    "bars_count": length,
                    "energy_level": energy_level,
                    "section_function": section_function,
                    "chord_progression": chords,
                    "chord_rhythm": chord_rhythm,
                    "arrangement": arrangement,
                    "transition_to_next": transition_to_next,
                }
            )

        if not sections_tmp:
            flow = list(getattr(concept, "structure_flow", []) or [])
            if not flow:
                flow = [type("TmpSection", (), {"name": "Main", "energy_curve": 0.7})()]
            cursor = 1
            for i, sec in enumerate(flow):
                length = 8
                sections_tmp.append(
                    {
                        "name": str(getattr(sec, "name", "") or f"Section {i + 1}"),
                        "index": i,
                        "start_bar": cursor,
                        "end_bar": cursor + length - 1,
                        "bars_count": length,
                        "energy_level": max(1, min(5, int(round(float(getattr(sec, "energy_curve", 0.6)) * 4 + 1)))),
                        "section_function": "推进",
                        "chord_progression": ["Cm"],
                        "chord_rhythm": "4bar",
                        "arrangement": self._default_arrangement(),
                        "transition_to_next": "smooth transition",
                    }
                )
                cursor += length

        sections_tmp.sort(key=lambda x: (int(x.get("start_bar", 1)), int(x.get("end_bar", 1)), int(x.get("index", 0))))

        # Best-effort: reflow to continuous timeline and reindex by order.
        normalized_sections: List[Dict[str, Any]] = []
        cursor = 1
        for i, sec in enumerate(sections_tmp):
            length = max(1, self._safe_int(sec.get("end_bar"), cursor) - self._safe_int(sec.get("start_bar"), cursor) + 1)
            sec2 = dict(sec)
            sec2["index"] = i
            sec2["start_bar"] = cursor
            sec2["end_bar"] = cursor + length - 1
            sec2["bars_count"] = length
            normalized_sections.append(sec2)
            cursor = sec2["end_bar"] + 1

        total_bars = max(1, cursor - 1)
        return {
            "concept": concept.model_dump(),
            "user_confirmed_duration_sec": float(user_confirmed_duration_sec),
            "total_bars": total_bars,
            "sections": normalized_sections,
        }

    async def agenerate_blueprint_from_concept(
        self,
        *,
        concept: SongConcept,
        user_request: str,
        user_confirmed_duration_sec: float,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        runnable_config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SongBlueprint:
        """Generate SongBlueprint from confirmed concept + duration."""
        duration_sec = float(user_confirmed_duration_sec)
        if duration_sec <= 40:
            section_count_hint = "2-3"
            max_tracks_per_section = 3
        elif duration_sec <= 90:
            section_count_hint = "3-4"
            max_tracks_per_section = 5
        else:
            section_count_hint = "4-6"
            max_tracks_per_section = 6
        max_tokens_budget = self._target_max_tokens(duration_sec)

        concept_json = json.dumps(concept.model_dump(), ensure_ascii=False, separators=(",", ":"))
        contract = (
            "{"
            '"concept": SongConcept(keep semantically consistent with provided concept),'
            '"user_confirmed_duration_sec": float,'
            '"total_bars": int,'
            '"sections": ['
            '{"name":str,"index":int,"start_bar":int,"end_bar":int,'
            '"energy_level":int(1..5),"section_function":str,'
            '"chord_progression":[str],"chord_rhythm":"8bar|4bar|2bar|1bar|2beats|1beat",'
            '"arrangement":{"<track_key>":{"role":str,"instrument_name":str,"playing_style":str,"mixing_hint":optional str}},'
            '"transition_to_next":str}'
            "]"
            "}"
        )
        rules = (
            "CRITICAL RULES:\n"
            "- Output ONLY valid JSON.\n"
            "- Match SongBlueprint exactly.\n"
            "- Keep concept values aligned with the provided concept input; avoid verbose rewrites.\n"
            "- user_confirmed_duration_sec must equal the provided confirmed value.\n"
            "- concept.structure_flow must match sections by order and musical intent.\n"
            f"- For this duration, target section count: {section_count_hint}.\n"
            "- sections must fully cover bars 1..total_bars without overlap or gaps.\n"
            "- section.index should be stable and start from 0 (0-based indexing).\n"
            "- section.energy_level must be integer in [1,5].\n"
            "- section.section_function should be one of: 铺垫/推进/爆发/转折/收尾 (or concise English equivalent).\n"
            "- chord_progression must be a list of chord symbols.\n"
            "- arrangement must include at least drums and bass, and each item must include role/instrument_name/playing_style.\n"
            "- arrangement.role must be one of: drum, bass, harmony, melody, fx.\n"
            "- arrangement track keys must be unique within each section.\n"
            f"- Keep arrangement compact: <= {max_tracks_per_section} tracks per section.\n"
            "- role layer count must be decided contextually from concept/style/energy/section function, not fixed globally.\n"
            "- low-energy or sparse sections can keep single-layer roles; high-energy dense sections can add more same-role layers.\n"
            "- when using multiple tracks for one role, each track must have a distinct timbre or musical function.\n"
            "- avoid over-layering: only add layers when they have clear arrangement value.\n"
            "- transition_to_next must be concrete and practical for non-last sections.\n"
            "- Keep output concise but specific; avoid placeholders.\n"
            "- Keep text fields short and plain (no embedded unescaped quotes).\n"
            "- Return one complete JSON object with all braces and brackets closed.\n"
        )

        prompt = (
            f"User request:\n{user_request}\n\n"
            f"Confirmed duration seconds: {duration_sec:.2f}\n\n"
            f"Concept input:\n{concept_json}\n\n"
            "Generate a complete production-ready SongBlueprint.\n\n"
            f"Compact output contract:\n{contract}\n\n"
            f"{rules}"
        )

        raw = await self.call_llm_async(
            user_prompt=prompt,
            response_model=None,
            structured_output_model=BlueprintDraft,
            temperature=self._temperature,
            max_tokens=max_tokens_budget,
            retries=self._retries,
            run_id=run_id,
            session_id=session_id,
            config=config,
            runnable_config=runnable_config,
            tags=tags,
            metadata=metadata,
        )
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        normalized = self._normalize_blueprint_payload(
            payload=raw,
            concept=concept,
            user_confirmed_duration_sec=duration_sec,
        )
        return SongBlueprint.model_validate(normalized)

    async def agenerate_blueprint(
        self,
        user_request: str,
        *,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        runnable_config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SongBlueprint:
        """Compatibility entry: generate concept then blueprint by inferred duration."""
        concept = await ConceptAgent().agenerate_concept(
            user_request,
            run_id=run_id,
            session_id=session_id,
            config=config,
            runnable_config=runnable_config,
            tags=tags,
            metadata=metadata,
        )
        duration = default_duration_from_concept(concept)
        return await self.agenerate_blueprint_from_concept(
            concept=concept,
            user_request=user_request,
            user_confirmed_duration_sec=duration,
            run_id=run_id,
            session_id=session_id,
            config=config,
            runnable_config=runnable_config,
            tags=tags,
            metadata=metadata,
        )

    def generate_skeleton(self, user_request: str):
        raise NotImplementedError(
            "generate_skeleton is deprecated. Use generate_blueprint and LangGraph orchestration instead."
        )

    def enrich_section(self, *args, **kwargs):
        raise NotImplementedError(
            "enrich_section is deprecated. Use generate_blueprint and LangGraph orchestration instead."
        )
