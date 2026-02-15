from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from schema.base import AgentRoutingRole
from schema.concept import SongConcept

# 和弦变化速率枚举：约束 LLM 输出可选值，便于下游统一处理。
ChordRhythmType = Literal["8bar", "4bar", "2bar", "1bar", "2beats", "1beat"]


class InstrumentDesign(BaseModel):
    """段落内单个乐器的设计信息。"""

    role: AgentRoutingRole
    instrument_name: str = Field(..., min_length=1, description="具体音色名称")
    playing_style: str = Field(..., min_length=1, description="演奏方式")
    mixing_hint: Optional[str] = Field(default=None, description="混音建议，可选")

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role_alias(cls, value):
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
    """蓝图中的工程段落定义（已落实到小节范围）。"""

    name: str = Field(..., min_length=1, description="段落名称")
    index: int = Field(..., ge=0, description="段落序号")

    start_bar: int = Field(..., ge=1, description="起始小节（含）")
    end_bar: int = Field(..., ge=1, description="结束小节（含）")
    bars_count: int = Field(..., ge=1, description="段落长度（小节数）")

    chord_progression: List[str] = Field(..., min_length=1, description="和弦进行")
    chord_rhythm: ChordRhythmType = Field(..., description="和弦变化速率")
    arrangement: Dict[str, InstrumentDesign] = Field(..., min_length=1, description="段落编配定义")
    transition_to_next: str = Field(..., description="到下一段的过渡说明")

    @model_validator(mode="after")
    def validate_bar_math(self):
        if self.end_bar < self.start_bar:
            raise ValueError("end_bar must be >= start_bar")

        expected = self.end_bar - self.start_bar + 1
        if self.bars_count != expected:
            raise ValueError(
                f"bars_count mismatch: expected {expected}, got {self.bars_count}"
            )
        return self


class SongBlueprint(BaseModel):
    """最终可执行蓝图：概念 + 用户确认时长 + 段落工程化结果。"""

    concept: SongConcept
    user_confirmed_duration_sec: float = Field(..., gt=0, description="用户确认总时长（秒）")
    total_bars: int = Field(..., ge=1, description="全曲总小节数")
    sections: List[DetailedSection] = Field(..., min_length=1, description="工程段落列表")

    @model_validator(mode="after")
    def validate_sections_timeline(self):
        # 按小节顺序检查连续性，禁止空洞和重叠。
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

        # index 需要与时间线同向增长，避免 section.index 与运行索引语义漂移。
        timeline_indexes = [sec.index for sec in ordered]
        if any(cur <= prev for prev, cur in zip(timeline_indexes, timeline_indexes[1:])):
            raise ValueError("section index must strictly increase with timeline order")

        # 同时保证唯一性。
        indexes = [sec.index for sec in self.sections]
        if len(set(indexes)) != len(indexes):
            raise ValueError("section index must be unique")

        return self
