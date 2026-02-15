import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from schema.base import AgentRoutingRole, GrooveIntent, NoteEvent, ScaleDefinition
from schema.blueprint_schema import ChordRhythmType, InstrumentDesign
from schema.section_schema import Strictness

_TIME_SIGNATURE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class LastBarTransitionNote(BaseModel):
    """过渡音符摘要：仅保留音高类与相对起点。"""

    pitch_class: int = Field(..., ge=0, le=11, description="音高类（0-11）")
    start_tick: int = Field(..., ge=0, description="相对最后一小节起点的量化 tick")


class LastBarMidiSummary(BaseModel):
    """上一段最后一小节压缩摘要，用于下段衔接。"""

    track_key: str = Field(..., min_length=1, description="来源轨道标识")
    instrument: Optional[AgentRoutingRole] = Field(default=None, description="来源乐器角色")
    notes: List[LastBarTransitionNote] = Field(default_factory=list, description="压缩后音符集合")
    bar_ticks: int = Field(..., ge=1, description="源段落每小节 tick 数")


class TrackContext(BaseModel):
    """依赖注入上下文：用于向下游 Agent 传递前文提取信息。"""

    # 场景 1: Drums -> Bass
    kick_onsets_ticks: List[int] = Field(default_factory=list, description="Kick 绝对 tick 位置列表")
    kick_summary_text: Optional[str] = Field(default=None, description="给 LLM 的简短 Kick 文本摘要")
    break_ranges: Optional[str] = Field(default=None, description="Break 小节压缩表示，如 1-3,7-8")
    kick_pitches: List[int] = Field(default_factory=list, description="摘要中使用的 Kick MIDI 音高")

    # 场景 2: Harmony -> Melody
    chord_notes_per_bar: List[List[int]] = Field(default_factory=list, description="每小节和弦构成音")

    # 场景 3: Melody -> FX
    lyric_rhythm_ticks: List[int] = Field(default_factory=list, description="旋律/歌词节奏 tick 列表")

    # 场景 4: Section -> Next Section
    prev_section_last_bar_midi: List[LastBarMidiSummary] = Field(
        default_factory=list,
        description="上一段最后一小节压缩 MIDI",
    )


class TrackGenRequest(BaseModel):
    """调度器发给各 Agent 的单轨生成请求。"""

    track_key: str = Field(..., description="轨道唯一标识，如 main_bass/sub_bass")
    compute_layer: int = Field(0, ge=0, description="生成优先级。0=无依赖，1=有依赖")

    section_index: int = Field(..., ge=0, description="运行时段落索引")
    section_name: str = Field(..., min_length=1, description="段落名称")
    instrument: AgentRoutingRole = Field(..., description="Agent 路由角色")
    midi_channel: Optional[int] = Field(
        default=None,
        ge=0,
        le=15,
        description="MIDI 通道（0-15），由运行时分配",
    )

    bpm: int = Field(..., gt=0, description="速度")
    time_signature: str = Field(..., description="拍号，如 4/4")
    ticks_per_beat: int = Field(480, ge=1, description="每拍 tick 数")
    bar_ticks: int = Field(..., ge=1, description="每小节 tick 数")
    start_bar: int = Field(..., ge=1, description="起始小节（含）")
    end_bar: int = Field(..., ge=1, description="结束小节（含）")

    chord_progression: List[str] = Field(default_factory=list, description="和弦进行")
    chord_rhythm: Optional[ChordRhythmType] = Field(default=None, description="和弦变化速率")

    style_description: Optional[str] = Field(default=None, description="风格描述")
    root_note: Optional[str] = Field(default=None, description="根音")
    scale: Optional[ScaleDefinition] = Field(default=None, description="调式定义")
    global_groove: Optional[GrooveIntent] = Field(default=None, description="全局律动意图")

    design: Optional[InstrumentDesign] = Field(default=None, description="轨道乐器设计")
    energy_level: Optional[float] = Field(default=None, description="段落能量")

    strictness: Strictness = Field(1, description="生成稳定度。0=创意，1=平衡，2=保守")
    context: Optional[TrackContext] = Field(default=None, description="依赖注入上下文")

    @field_validator("time_signature")
    @classmethod
    def normalize_time_signature(cls, value: str) -> str:
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


class GeneratedTrack(BaseModel):
    """Agent 生成结果：包含音符、原始输出与错误信息。"""

    track_key: str = Field(..., description="轨道唯一标识")
    instrument: AgentRoutingRole = Field(..., description="路由角色")
    section_name: str = Field(..., description="段落名称")
    channel: int = Field(0, ge=0, le=15, description="MIDI 通道")

    notes: List[NoteEvent] = Field(default_factory=list, description="扁平化音符列表")
    raw_output: Optional[Any] = Field(default=None, description="原始结构化输出")
    error: Optional[str] = Field(default=None, description="错误信息")
