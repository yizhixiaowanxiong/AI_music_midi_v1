from __future__ import annotations

"""编排阶段与轨道生成阶段共享的数据结构。

本文件主要定义三类模型：
1) 运行时上下文 `TrackContext`：用于轨道间的轻量信息传递。
2) 轨道生成请求 `TrackGenRequest`：调度器发给各轨道 Agent 的输入。
3) 轨道生成结果 `GeneratedTrack`：Agent 返回给调度器的产物。
"""

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from schema.base import AgentRoutingRole, NoteEvent, ScaleDefinition
from schema.blueprint_schema import ChordRhythmType, InstrumentDesign
from schema.section_schema import Strictness

_TIME_SIGNATURE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class LastBarTransitionNote(BaseModel):
    """跨段衔接用的压缩音符信息（只保留音级与相对起点）。"""

    pitch_class: int = Field(..., ge=0, le=11)
    start_tick: int = Field(..., ge=0)


class LastBarMidiSummary(BaseModel):
    """上一段末小节的压缩 MIDI 摘要。"""

    track_key: str = Field(..., min_length=1)
    instrument: Optional[AgentRoutingRole] = None
    notes: List[LastBarTransitionNote] = Field(default_factory=list)
    bar_ticks: int = Field(..., ge=1)


class PitchRange(BaseModel):
    """结构化音高占用范围（用于代码侧可计算冲突检测）。"""

    low_midi: int = Field(..., ge=0, le=127)
    high_midi: int = Field(..., ge=0, le=127)

    @model_validator(mode="after")
    def validate_range(self):
        if self.high_midi < self.low_midi:
            raise ValueError("high_midi must be >= low_midi")
        return self

    def as_text(self) -> str:
        return f"MIDI[{self.low_midi}-{self.high_midi}]"


class RhythmConstraint(BaseModel):
    """结构化节奏约束（用于代码侧精确校验与统计）。"""

    kind: str = Field(..., min_length=1, description="约束类型，如 align_to_kick/avoid_same_tick")
    anchor_ticks: List[int] = Field(default_factory=list, description="锚点 tick（可选）")
    payload: dict[str, Any] = Field(default_factory=dict, description="额外参数")

    def as_text(self) -> str:
        if self.anchor_ticks:
            ticks = ",".join(str(int(x)) for x in list(self.anchor_ticks)[:8])
            return f"{self.kind}({ticks})"
        if self.payload:
            parts = [f"{k}={v}" for k, v in list(self.payload.items())[:4]]
            return f"{self.kind}({';'.join(parts)})"
        return self.kind


class TrackContext(BaseModel):
    """轨道间运行时上下文（轻量版）。

    设计原则：
    - 只存“约束”和“摘要”，不存完整音符序列。
    - 避免上下文膨胀，减少 LLM token 与超时风险。
    """

    # Drums -> Bass：底鼓起点（用于节奏对齐）
    kick_onsets_ticks: List[int] = Field(default_factory=list)

    # Harmony -> Melody：每小节和声音高摘要
    chord_notes_per_bar: List[List[int]] = Field(default_factory=list)

    # Melody -> FX：旋律起点摘要
    lyric_rhythm_ticks: List[int] = Field(default_factory=list)

    # Section -> Next Section：跨段衔接摘要
    prev_section_last_bar_midi: List[LastBarMidiSummary] = Field(default_factory=list)

    # 避让规则（核心）：频段占用 + 节奏约束 + 动机
    occupied_frequency_bands: List[str] = Field(default_factory=list)
    locked_rhythm_rules: List[str] = Field(default_factory=list)
    core_motif: Optional[str] = None

    # 结构化版本（仅用于代码侧校验，不直接依赖 LLM 自然语言）
    occupied_pitch_ranges: List[PitchRange] = Field(default_factory=list)
    rhythm_constraints: List[RhythmConstraint] = Field(default_factory=list)

    def to_rule_text(self) -> str:
        """将结构化规则压缩成可直接喂给 LLM 的短文本。"""
        rhythm_rules = [str(x).strip() for x in list(self.locked_rhythm_rules or []) if str(x).strip()]
        freq_bands = [str(x).strip() for x in list(self.occupied_frequency_bands or []) if str(x).strip()]

        # 当字符串规则缺失时，回落到结构化字段派生，避免丢失约束信息。
        if not freq_bands and self.occupied_pitch_ranges:
            freq_bands = [item.as_text() for item in self.occupied_pitch_ranges[:4]]
        if not rhythm_rules and self.rhythm_constraints:
            rhythm_rules = [item.as_text() for item in self.rhythm_constraints[:4]]

        rules: List[str] = []
        if rhythm_rules:
            rules.append(f"Rhythm Constraints: {'; '.join(rhythm_rules)}")
        if freq_bands:
            rules.append(f"Occupied Freqs (avoid overlap): {', '.join(freq_bands)}")
        motif = str(self.core_motif or "").strip()
        if motif:
            rules.append(f"Core Motif: {motif}")
        return "\n".join(rules) if rules else "No specific constraints."


class TrackGenRequest(BaseModel):
    """单条轨道生成请求。"""

    # 轨道身份
    track_key: str
    compute_layer: int = Field(0, ge=0)

    # 段落身份
    section_index: int = Field(..., ge=0)
    section_name: str = Field(..., min_length=1)
    instrument: AgentRoutingRole
    midi_channel: Optional[int] = Field(default=None, ge=0, le=15)

    # 时间与节拍
    bpm: int = Field(..., gt=0)
    time_signature: str
    ticks_per_beat: int = Field(480, ge=1)
    bar_ticks: int = Field(..., ge=1)
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)

    # 和声输入
    chord_progression: List[str] = Field(default_factory=list)
    chord_rhythm: Optional[ChordRhythmType] = None

    # 风格输入
    style_description: Optional[str] = None
    scale: Optional[ScaleDefinition] = None

    # 轨道设计与控制
    design: Optional[InstrumentDesign] = None
    energy_level: Optional[float] = None
    strictness: Strictness = 1

    # 上下文（三层 + 运行时）
    context: Optional[TrackContext] = None
    global_anchor_summary: Optional[str] = None
    section_summary: Optional[str] = None
    context_summary: Optional[str] = Field(
        default=None,
        description="内部合成摘要（由分层摘要 + 避让规则派生），外部无需直接填写。",
        json_schema_extra={"readOnly": True},
    )

    @field_validator("time_signature")
    @classmethod
    def normalize_time_signature(cls, value: str) -> str:
        """统一拍号格式为 N/D 并做合法性检查。"""
        match = _TIME_SIGNATURE_RE.match(str(value or ""))
        if not match:
            raise ValueError("time_signature must be in 'numerator/denominator' format")
        numerator = int(match.group(1))
        denominator = int(match.group(2))
        if numerator <= 0 or denominator <= 0:
            raise ValueError("time_signature numerator/denominator must be > 0")
        if denominator not in (1, 2, 4, 8, 16, 32):
            raise ValueError("time_signature denominator must be one of 1,2,4,8,16,32")
        return f"{numerator}/{denominator}"

    @model_validator(mode="after")
    def derive_context_summary(self):
        """统一派生 context_summary，避免上游节点重复拼接与格式漂移。"""
        if str(self.context_summary or "").strip():
            return self

        parts: List[str] = []
        for item in (self.global_anchor_summary, self.section_summary):
            text = str(item or "").strip()
            if text and text not in parts:
                parts.append(text)

        ctx = self.context
        if ctx is not None:
            rule_text = str(ctx.to_rule_text() or "").strip()
            if rule_text and rule_text != "No specific constraints." and rule_text not in parts:
                parts.append(rule_text)

        self.context_summary = "\n".join(parts) if parts else None
        return self


class GeneratedTrack(BaseModel):
    """单条轨道输出。"""

    track_key: str
    instrument: AgentRoutingRole
    section_name: str
    channel: int = Field(0, ge=0, le=15)

    notes: List[NoteEvent] = Field(default_factory=list)
    raw_output: Optional[Any] = None

    # 运行指标（如 context_budget）
    metrics: Optional[dict[str, Any]] = None
    error: Optional[str] = None
