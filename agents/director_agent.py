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
    @staticmethod
    def _required_bars(bpm: int, time_signature: str, duration_sec: int) -> int:
        """Calculate how many bars are needed to fill the target duration."""
        try:
            n, d = (int(x) for x in str(time_signature).split("/"))
        except Exception:
            n, d = 4, 4
        beats_per_bar = n * (4 / d)
        bars = (duration_sec * bpm) / (beats_per_bar * 60)
        return max(1, round(bars))

    async def generate_blueprint(self, concept: SongConcept, user_prompt: str = "") -> SongBlueprint:
        required_bars = self._required_bars(
            concept.bpm, concept.time_signature, concept.target_duration_sec,
        )
        prompt = f"""
Role: Music Director.
Task: Create a song structure (list of sections).

Style: {concept.style_description} ({concept.mood})
BPM: {concept.bpm} | Time Sig: {concept.time_signature}
Key: {concept.key_root} {concept.key_mode}
Target Duration: {concept.target_duration_sec}s

Bar Budget: At {concept.bpm}BPM in {concept.time_signature}, {concept.target_duration_sec}s ≈ {required_bars} bars. The sum of all section length_bars MUST equal {required_bars}.

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
