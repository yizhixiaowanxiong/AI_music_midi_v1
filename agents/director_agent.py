# agents/director_agent.py
import json
from core.base_agent import BaseAgent
from schema.blueprint_schema import SongBlueprint, Section, PhrasePlan
from schema.blueprint_skeleton_schema import SongBlueprintSkeleton


class DirectorAgent(BaseAgent):
    def __init__(self):
        super().__init__(system_prompt="You are a professional music director.")

    def generate_blueprint(self, user_request: str) -> SongBlueprint:
        schema_json = json.dumps(SongBlueprint.model_json_schema(), indent=2)

        musical_rules = (
            "CRITICAL BUSINESS RULES:\n"
            "- sections must fully cover 1..total_bars with no overlap.\n"
            "- Must include Drop and Drop has the highest global_energy.\n"
            "- chord_progression is a list of chord symbols.\n"
            "- You MUST set chord_rhythm + repeat so that:\n"
            "  section_len_bars == len(chord_progression) * bars_per_chord(chord_rhythm) * repeat\n"
            "- If section_len_bars >= 16, you MUST provide phrases covering the whole section.\n"
            "\n"
            "MUSICALITY GUIDANCE (High Priority):\n"
            "- Intro: avoid a single-chord loop for 8 bars; use at least 2 chords or longer chord_rhythm.\n"
            "- Build-up: the final phrase should hint a motif (lead light) or stronger chord stabs.\n"
            "- Drop: fx should be 'fill' or 'full' to support transitions.\n"
            "- Provide specific playing_style text (avoid 'none' unless role is silent).\n"
            "- Ensure instruments have varied roles (not everything is 'support').\n"
        )

        combined_prompt = (
            f"User Request: {user_request}\n\n"
            f"### INSTRUCTIONS ###\n"
            f"1. Output valid JSON strictly matching the schema below.\n"
            f"2. Follow the Musicality Guidance provided.\n\n"
            f"### JSON SCHEMA ###\n"
            f"{schema_json}\n\n"
            f"### GUIDELINES ###\n"
            f"{musical_rules}"
        )

        blueprint = self.call_llm(
            user_prompt=combined_prompt,
            response_model=SongBlueprint,
            temperature=0.3,
            max_tokens=1400,
        )

        return blueprint

    def generate_skeleton(self, user_request: str) -> SongBlueprintSkeleton:
        schema_json = json.dumps(SongBlueprintSkeleton.model_json_schema(), indent=2)

        rules = (
            "RULES:\n"
            "- Output ONLY valid JSON matching the schema.\n"
            "- Sections must fully cover 1..total_bars without overlap.\n"
            "- Must include a Drop section.\n"
            "- Keep chord_progression short (2-4 chords).\n"
        )

        prompt = (
            f"User Request: {user_request}\n\n"
            f"### INSTRUCTIONS ###\n"
            f"1. Output valid JSON strictly matching the schema below.\n"
            f"2. Provide only coarse structure (no arrangement or phrases).\n\n"
            f"### JSON SCHEMA ###\n"
            f"{schema_json}\n\n"
            f"### RULES ###\n"
            f"{rules}"
        )

        skeleton = self.call_llm(
            user_prompt=prompt,
            response_model=SongBlueprintSkeleton,
            temperature=0.3,
            max_tokens=700,
        )
        return skeleton

    def enrich_section(
        self,
        skeleton: SongBlueprintSkeleton,
        section_index: int,
    ) -> Section:
        if section_index < 0 or section_index >= len(skeleton.sections):
            raise ValueError("section_index out of range.")
        sec = skeleton.sections[section_index]

        schema_json = json.dumps(Section.model_json_schema(), indent=2)
        rules = (
            "RULES:\n"
            "- Output ONLY valid JSON matching the Section schema.\n"
            "- Keep start_bar/end_bar/name consistent with the given section.\n"
            "- Ensure section_len_bars == len(chord_progression) * bars_per_chord(chord_rhythm) * repeat when progression_is_loop.\n"
            "- For short sections, reduce chord_rhythm or repeat to fit section length.\n"
            "- Provide arrangement for at least drums and bass.\n"
            "- If section_len_bars >= 16, phrases must fully cover the section.\n"
        )

        prompt = (
            f"Skeleton Section:\n{sec.model_dump_json(indent=2)}\n\n"
            f"Song Context:\n"
            f"- bpm: {skeleton.bpm}\n"
            f"- time_signature: {skeleton.time_signature}\n"
            f"- key: {skeleton.root_note} {skeleton.scale}\n"
            f"- style: {skeleton.style_description}\n\n"
            f"### INSTRUCTIONS ###\n"
            f"Fill in detailed fields for this section only.\n"
            f"Output valid JSON matching the Section schema below.\n\n"
            f"### JSON SCHEMA ###\n"
            f"{schema_json}\n\n"
            f"### RULES ###\n"
            f"{rules}"
        )

        section_detail = self.call_llm(
            user_prompt=prompt,
            response_model=Section,
            temperature=0.3,
            max_tokens=900,
        )
        # Fallback: ensure phrases exist for Drop/Outro to avoid empty phrase plans downstream.
        if section_detail.name in ("Drop", "Outro") and not section_detail.phrases:
            section_detail.phrases = [
                PhrasePlan(
                    start_bar=section_detail.start_bar,
                    end_bar=section_detail.end_bar,
                    arrangement_override={},
                )
            ]
        return section_detail
