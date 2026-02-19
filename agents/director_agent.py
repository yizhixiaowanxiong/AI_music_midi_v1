"""agents/director_agent.py - 蓝图规划。"""

from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, model_validator

from agents.base_agent import BaseAgent
from schema.blueprint_schema import SkeletonSection, SongBlueprint
from schema.concept import SongConcept, _unwrap_nested


class SectionList(BaseModel):
    sections: List[SkeletonSection]

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        return _unwrap_nested(data, {"sections"})


class DirectorAgent(BaseAgent):
    async def generate_blueprint(self, concept: SongConcept, user_prompt: str = "") -> SongBlueprint:
        prompt = f"""
Role: Music Director.
Task: Create a song structure (list of sections).

Style: {concept.style_description} ({concept.mood})
BPM: {concept.bpm} | Time Sig: {concept.time_signature}
Key: {concept.key_root} {concept.key_mode}
Target Duration: {concept.target_duration_sec}s

User Note: {str(user_prompt or '').strip()}

Output: JSON object with 'sections' list.
Each section needs: name, length_bars, energy_level(1-5), chord_progression.
"""
        result = await self.structured_generate(prompt, SectionList)

        return SongBlueprint(
            user_confirmed_duration_sec=concept.target_duration_sec,
            sections=result.sections,
        )

    async def agenerate_blueprint_from_concept(
        self,
        concept: SongConcept,
        user_request: str,
        user_confirmed_duration_sec: float,
        **_kwargs,
    ) -> SongBlueprint:
        """兼容 orchestrator 旧接口。"""
        normalized = concept.model_copy(
            update={"target_duration_sec": int(max(1, round(float(user_confirmed_duration_sec))))}
        )
        return await self.generate_blueprint(normalized, user_prompt=user_request)
