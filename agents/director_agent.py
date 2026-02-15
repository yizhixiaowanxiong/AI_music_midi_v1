"""Director Agent：负责从 concept 生成可执行编曲蓝图。"""

from __future__ import annotations

import json
import re

from agents.concept_agent import ConceptAgent
from agents.llm_base_agent import BaseAgent
from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept


class DirectorAgent(BaseAgent):
    """编曲导演 Agent，负责 Blueprint 级规划。"""

    def __init__(self):
        super().__init__(system_prompt="You are a professional music director and arranger.")

    def _default_duration_from_concept(self, concept: SongConcept) -> float:
        """从 concept 的建议时长文本中推断默认秒数。"""
        text = str(getattr(concept, "suggested_duration_range", "") or "")
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if len(nums) >= 2:
            return (float(nums[0]) + float(nums[1])) / 2.0
        if len(nums) == 1:
            return float(nums[0])
        return 180.0

    async def agenerate_blueprint_from_concept(
        self,
        *,
        concept: SongConcept,
        user_request: str,
        user_confirmed_duration_sec: float,
    ) -> SongBlueprint:
        """基于已确认时长与 concept，生成可执行 SongBlueprint。"""
        concept_json = json.dumps(concept.model_dump(), ensure_ascii=False, indent=2)
        contract = (
            "{"
            '"concept": SongConcept(keep semantically consistent with provided concept),'
            '"user_confirmed_duration_sec": float,'
            '"total_bars": int,'
            '"sections": ['
            '{"name":str,"index":int,"start_bar":int,"end_bar":int,"bars_count":int,'
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
            "- Keep concept consistent with the provided concept input.\n"
            "- user_confirmed_duration_sec must equal the provided confirmed value.\n"
            "- concept.structure_flow must match sections by order and musical intent.\n"
            "- sections must fully cover bars 1..total_bars without overlap or gaps.\n"
            "- For each section, bars_count must equal end_bar - start_bar + 1.\n"
            "- section.index should be stable and start from 0 (0-based indexing).\n"
            "- chord_progression must be a list of chord symbols.\n"
            "- arrangement must include at least drums and bass, and each item must include role/instrument_name/playing_style.\n"
            "- arrangement.role must be one of: percussion, bass, harmony, melody, fx.\n"
            "- arrangement track keys must be unique within each section.\n"
            "- role layer count must be decided contextually from concept/style/energy/section function, not fixed globally.\n"
            "- low-energy or sparse sections can keep single-layer roles; high-energy dense sections can add more same-role layers.\n"
            "- when using multiple tracks for one role, each track must have a distinct timbre or musical function.\n"
            "- avoid over-layering: only add layers when they have clear arrangement value.\n"
            "- transition_to_next must be concrete and practical for non-last sections.\n"
            "- Keep output concise but specific; avoid placeholders.\n"
        )

        prompt = (
            f"User request:\n{user_request}\n\n"
            f"Confirmed duration seconds: {float(user_confirmed_duration_sec):.2f}\n\n"
            f"Concept input:\n{concept_json}\n\n"
            "Generate a complete production-ready SongBlueprint.\n\n"
            f"Compact output contract:\n{contract}\n\n"
            f"{rules}"
        )

        return await self.call_llm_async(
            user_prompt=prompt,
            response_model=SongBlueprint,
            temperature=0.3,
            max_tokens=1400,
        )

    async def agenerate_blueprint(self, user_request: str) -> SongBlueprint:
        """兼容入口：先生成 concept，再按默认时长生成 blueprint。"""
        concept = await ConceptAgent().agenerate_concept(user_request)
        duration = self._default_duration_from_concept(concept)
        return await self.agenerate_blueprint_from_concept(
            concept=concept,
            user_request=user_request,
            user_confirmed_duration_sec=duration,
        )

    # 旧接口显式弃用，防止上层误调用。
    def generate_skeleton(self, user_request: str):
        raise NotImplementedError(
            "generate_skeleton is deprecated. Use generate_blueprint and LangGraph orchestration instead."
        )

    # 旧接口显式弃用，防止上层误调用。
    def enrich_section(self, *args, **kwargs):
        raise NotImplementedError(
            "enrich_section is deprecated. Use generate_blueprint and LangGraph orchestration instead."
        )
