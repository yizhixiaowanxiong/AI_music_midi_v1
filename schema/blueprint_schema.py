"""蓝图层（Blueprint）schema。

蓝图是 Concept 的工程化展开：
- 明确每段 start/end bar
- 明确和弦、编配与过渡
- 可直接驱动后续轨道生成
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from schema.base import AgentRoutingRole
from schema.concept import SongConcept

# 和弦变化速率（约束 LLM 输出候选值）
ChordRhythmType = Literal["8bar", "4bar", "2bar", "1bar", "2beats", "1beat"]


class InstrumentDesign(BaseModel):
    """段落内单个轨道的设计信息。"""

    role: AgentRoutingRole
    instrument_name: str = Field(..., min_length=1, description="具体音色名称")
    playing_style: str = Field(..., min_length=1, description="演奏方式")
    mixing_hint: Optional[str] = Field(default=None, description="混音提示，可选")

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role_alias(cls, value):
        """兼容角色别名输入。"""
        if isinstance(value, AgentRoutingRole):
            return value
        text = str(value or "").strip().lower()
        alias_map = {
            "drums": AgentRoutingRole.PERCUSSION,
            "drum": AgentRoutingRole.PERCUSSION,
            "rhythm": AgentRoutingRole.PERCUSSION,
            "percussion": AgentRoutingRole.PERCUSSION,
            "bassline": AgentRoutingRole.BASS,
            "lowend": AgentRoutingRole.BASS,
            "sub": AgentRoutingRole.BASS,
            "bass": AgentRoutingRole.BASS,
            "chords": AgentRoutingRole.HARMONY,
            "pad": AgentRoutingRole.HARMONY,
            "atmosphere": AgentRoutingRole.HARMONY,
            "harmony": AgentRoutingRole.HARMONY,
            "lead": AgentRoutingRole.MELODY,
            "melody": AgentRoutingRole.MELODY,
            "texture": AgentRoutingRole.FX,
            "noise": AgentRoutingRole.FX,
            "fx": AgentRoutingRole.FX,
            "effect": AgentRoutingRole.FX,
        }
        return alias_map.get(text, value)


class DetailedSection(BaseModel):
    """蓝图中的段落定义（已落实到小节范围）。"""

    name: str = Field(..., min_length=1, description="段落名称")
    index: int = Field(..., ge=0, description="段落序号")

    start_bar: int = Field(..., ge=1, description="起始小节（含）")
    end_bar: int = Field(..., ge=1, description="结束小节（含）")
    bars_count: Optional[int] = Field(
        default=None,
        ge=1,
        description="已废弃输入：段落长度。实际由 start_bar/end_bar 推导。",
        json_schema_extra={"deprecated": True},
    )
    energy_level: int = Field(3, ge=1, le=5, description="段落能量等级（1-5）")
    section_function: str = Field("推进", description="段落功能（铺垫/推进/爆发/转折/收尾）")

    chord_progression: List[str] = Field(..., min_length=1, description="和弦进行")
    chord_rhythm: ChordRhythmType = Field(..., description="和弦变化速率")
    arrangement: Dict[str, InstrumentDesign] = Field(..., min_length=1, description="段落编配定义")
    transition_to_next: str = Field(..., description="到下一段的过渡说明")

    @model_validator(mode="after")
    def validate_bar_math(self):
        if self.end_bar < self.start_bar:
            raise ValueError("end_bar must be >= start_bar")

        expected = self.end_bar - self.start_bar + 1
        # 统一改为派生值，避免要求 LLM 额外输出冗余字段
        self.bars_count = expected
        return self


class SongBlueprint(BaseModel):
    """可执行蓝图：Concept + 用户确认时长 + 工程段落列表。"""

    concept: SongConcept
    user_confirmed_duration_sec: float = Field(..., gt=0, description="用户确认总时长（秒）")
    total_bars: int = Field(..., ge=1, description="全曲总小节数")
    sections: List[DetailedSection] = Field(..., min_length=1, description="工程段落列表")

    @model_validator(mode="after")
    def validate_sections_timeline(self):
        """校验段落时间线连续且 index 语义稳定。"""
        ordered = sorted(self.sections, key=lambda s: (s.start_bar, s.end_bar))
        expected_start = 1
        for sec in ordered:
            if sec.start_bar != expected_start:
                raise ValueError(
                    "sections must be continuous without gaps/overlaps: "
                    f"expected start_bar={expected_start}, got {sec.start_bar} ({sec.name})"
                )
            expected_start = sec.end_bar + 1

        if ordered[-1].end_bar != self.total_bars:
            raise ValueError(
                f"total_bars mismatch: last end_bar={ordered[-1].end_bar}, total_bars={self.total_bars}"
            )

        timeline_indexes = [sec.index for sec in ordered]
        if any(cur <= prev for prev, cur in zip(timeline_indexes, timeline_indexes[1:])):
            raise ValueError("section index must strictly increase with timeline order")

        indexes = [sec.index for sec in self.sections]
        if len(set(indexes)) != len(indexes):
            raise ValueError("section index must be unique")

        return self
