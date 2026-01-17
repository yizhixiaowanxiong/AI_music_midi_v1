from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Dict

# -----------------------------
# 1. 全局枚举与基础类型
# -----------------------------
ScaleType = Literal["major", "minor", "dorian", "phrygian", "lydian"] #调式
SectionName = Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"] #乐段名字
Density = Literal["sparse", "medium", "dense"] #密度值

# 对于各个乐器演奏的变体标签：用于编排层挑选
VariantTag = Literal["mute", "core", "light", "full", "build", "break", "fill"]


# -----------------------------
# 2. Groove：意图层 (Renderer 使用)
# 数值映射交给 Renderer（稳定、可控）
# -----------------------------
class GrooveIntent(BaseModel):
    """
    让 LLM 输出“档位”，Renderer 再映射到具体数值（ms/ticks/vel）
    """
    humanize_level: Literal["off", "low", "mid", "high"] = "mid" #人性化水平
    swing: Literal["off", "low", "mid", "high"] = "mid" #浮动力度
    feel: Literal["tight", "neutral", "laid_back"] = "neutral" #抢/拖拍


# -----------------------------
# 3. 音符结构 (Note)
# 重要：start_time 必须是“绝对 tick”（建议相对曲首），别写“相对小节”
# -----------------------------
class NoteSchema(BaseModel):
    pitch: int = Field(..., description="MIDI pitch 0-127; C4=60", ge=0, le=127) #音高
    start_tick: int = Field(..., description="absolute start tick from song start", ge=0) #音符开始时间
    duration_tick: int = Field(..., description="duration in ticks", ge=1) #持续时间
    velocity: int = Field(90, description="0-127", ge=0, le=127) #演奏力度


# -----------------------------
# 4. 自动化 (Automation)
# -----------------------------
class AutomationSchema(BaseModel):
    #  音量、声像、截止频率、共鸣。
    type: Literal["volume", "pan", "cutoff", "resonance"]
    start_val: int = Field(..., ge=0, le=127) #开始值
    end_val: int = Field(127, ge=0, le=127) #结束值
    start_tick: int = Field(..., ge=0) #开始时间
    duration_tick: int = Field(..., ge=1) #持续时间
    curve: Literal["linear", "exponential"] = "linear" #变化曲线


# -----------------------------
# 5. Track：支持“核心 + 变体”
# LLM 默认只生成 core，其他变体由 Renderer 派生；
# -----------------------------
class TrackVariant(BaseModel):
    tag: VariantTag = "core" #标签
    notes: List[NoteSchema] = Field(default_factory=list) #音符列表
    automations: List[AutomationSchema] = Field(default_factory=list) #自动化参数列表


class TrackSchema(BaseModel):
    name: str = Field(..., description="Track name") #音轨名称
    instrument: str = Field(..., description="Instrument type") #乐器名称
    channel: int = Field(0, ge=0, le=15) #乐器名称
    groove_intent: GrooveIntent = Field(default_factory=GrooveIntent) #可变性参数

    # 核心与变体
    variants: List[TrackVariant] = Field(default_factory=list)

    @field_validator('variants')
    @classmethod
    def ensure_core_variant(cls, v: List[TrackVariant]):
        # 保障至少有一个 core，不然影响变体生成
        if not any(x.tag == "core" for x in v):
            v = [TrackVariant(tag="core")] + v
        return v


# -----------------------------
# 6. TrackSummary：上下文传递专用 节省token
# -----------------------------
class TrackSummary(BaseModel):
    name: str
    instrument: str
    active_bars: List[int] = Field(default_factory=list)
    density: Density = "medium"
    kick_onsets_preview: Optional[List[int]] = None
    chord_progression: Optional[List[str]] = None
    note_range: Optional[str] = None


# -----------------------------
# 7. 指挥官指令与段落定义 (关键修正部分)
# -----------------------------

# 先定义具体的指令
class InstrumentInstruction(BaseModel):
    """
    指挥官给特定乐器的具体指令
    """
    role: Literal["silent", "background", "support", "lead", "solo"] = Field(..., description="乐器角色") #指定乐器状态
    playing_style: str = Field(..., description="演奏风格建议") #演奏风格
    energy_level: float = Field(..., description="能量值 0.0-1.0", ge=0.0, le=1.0) #能量值

    # 可选：如果指挥官想指定用哪个变体
    variant_tag: Optional[VariantTag] = Field("core", description="建议使用的变体标签") #指定的变体类型


# 再定义段落 (Section)
class Section(BaseModel):
    """
    歌曲的一个段落
    """
    name: SectionName = Field(..., description="段落名称") #段落名称
    start_bar: int = Field(..., description="开始小节号") #开始小节号
    end_bar: int = Field(..., description="结束小节号") #结束小节号
    global_energy: float = Field(..., description="段落总能量 0.0-1.0", ge=0.0, le=1.0) #段落总能量

    # 和弦进行
    chord_progression: List[str] = Field(..., description="e.g. ['Cm', 'Fm']")

    # 核心：分轨指挥字典。Key 是乐器名 (drum, bass, pad, lead)
    arrangement: Dict[str, InstrumentInstruction] = Field(..., description="各乐器的编排指令")


# 最后定义总谱 (Blueprint)
class SongBlueprint(BaseModel):
    """
    Step 0: 指挥官产出的总谱
    """
    song_name: str #曲名
    bpm: int #曲速
    scale: str #调式
    time_signature: str = "4/4" #节拍类型
    total_bars: int #总小节数
    style_description: str #风格描述

    # 歌曲结构列表
    sections: List[Section]


# -----------------------------
# 8. 编曲输出结构 (最终整合用)
# -----------------------------
class ArrangementSchema(BaseModel):
    """
    Agent 最终产出的完整编曲数据 (落盘用)
    """
    song_name: str #曲名
    bpm: int #曲速
    scale: str #调式
    root_note: str = "C"  # 根音
    total_bars: int #总小节数
    tracks: List[TrackSchema] #轨道