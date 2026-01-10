from pydantic import BaseModel,Field,field_validator
from typing import List,Optional,Literal,Dict

# -----------------------------
# 1.全局枚举与基础类型
ScaleType = Literal["major", "minor", "dorian", "phrygian", "lydian"] #调性

SectionName = Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"] #每个乐段的名字

Density = Literal["sparse", "medium", "dense"] #密度

# 变体标签：用于编排层挑选
VariantTag = Literal[
    "mute",      # 不用
    "core",      # 核心版本
    "light",     # 轻量
    "full",      # 丰满
    "build",     # build-up 专用（上升/加密）
    "break",     # breakdown 专用（减法/留白）
    "fill"       # 过门
]


# -----------------------------
# 2) Groove：意图层
#    数值映射交给 Renderer（稳定、可控）
class GrooveIntent(BaseModel):
    """
    让 LLM 输出“感觉档位”，Renderer 再映射到具体数值（ms/ticks/vel）
    """
    humanize_level: Literal["off", "low", "mid", "high"] = "mid" #人性化水平
    swing: Literal["off", "low", "mid", "high"] = "mid" #浮动力度
    feel: Literal["tight", "neutral", "laid_back"] = "neutral"  # 推/拉（抢拍/拖拍）

# # new-1.结构标记
# class SectionMarker(BaseModel):
#     name: Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"] = Field(..., description="段落名称")
#     start_time:int=Field(...,description="该段落开始的时间",ge=0)

# -----------------------------
# 3) 音符结构：只保留“音乐本体数据”
#    重要：start_time 必须是“绝对 tick”（建议相对曲首），别写“相对小节”
# -----------------------------
class NoteSchema(BaseModel):
    pitch: int = Field(..., description="MIDI pitch 0-127; C4=60", ge=0, le=127) #音符音高
    start_tick: int = Field(..., description="absolute start tick from song start", ge=0) #音符开始时间
    duration_tick: int = Field(..., description="duration in ticks", ge=1) #持续时间
    velocity: int = Field(90, description="0-127", ge=0, le=127) #演奏力度

# -----------------------------
# 4) 自动化
# -----------------------------
class AutomationSchema(BaseModel):
    # 音量、声像、截止频率、共鸣。
    type: Literal["volume", "pan", "cutoff", "resonance"] = Field(..., description="CC-like automation")
    start_val: int = Field(..., ge=0, le=127) #开始值
    end_val: int = Field(127, ge=0, le=127) #结束值
    start_tick: int = Field(..., description="absolute start tick", ge=0) #开始时间
    duration_tick: int = Field(..., description="duration in ticks", ge=1) #持续时间
    curve: Literal["linear", "exponential"] = "linear" #变化曲线

# -----------------------------
# 5) Track：支持“核心 + 变体”
#    LLM 默认只生成 core，其他变体由 Renderer 派生；
# -----------------------------
class TrackVariant(BaseModel):
    tag: VariantTag = "core" #变体标识
    notes: List[NoteSchema] = Field(default_factory=list) #音符列表
    automations: List[AutomationSchema] = Field(default_factory=list) #自动化参数列表

class TrackSchema(BaseModel):
    name: str = Field(..., description="Track name, e.g. 'Bass'")  #音轨名称
    instrument: str = Field(..., description="Instrument type, e.g. 'drum', 'bass_synth'") #乐器名称
    channel: int = Field(0, ge=0, le=15) #通道序号
    groove_intent: GrooveIntent = Field(default_factory=GrooveIntent) #可变性参数

    # 核心与变体
    variants: List[TrackVariant] = Field(default_factory=list)

    @field_validator('variants')
    @classmethod
    def ensure_core_variant(cls, v: List[TrackVariant]):
        # 保障至少有一个 core（否则后续编排难做）
        if not any(x.tag == "core" for x in v):
            v = [TrackVariant(tag="core")] + v
        return v

# -----------------------------
# 6) TrackSummary：上下文传递专用（省 token）
# -----------------------------
class TrackSummary(BaseModel):
    name: str
    instrument: str
    active_bars: List[int] = Field(default_factory=list)
    density: Density = "medium"

    # drums 特化
    kick_onsets_preview: Optional[List[int]] = None  # 只放前几小节的 kick onsets

    # harmony 特化
    chord_progression: Optional[List[str]] = None    # ["Cm", "Ab", "Bb", "Gm"]

    note_range: Optional[str] = None                 # e.g. "C2-C3"

# -----------------------------
# 7) Blueprint：总导演蓝图（Step 0 产物）
#    关键新增：arrangement_plan + presence_targets
# -----------------------------
class SectionPlan(BaseModel):
    name: SectionName #表明乐段身份
    start_bar: int = Field(..., ge=1) #乐段起始小节
    end_bar: int = Field(..., ge=1)
    energy: float = Field(..., ge=0.0, le=1.0) #乐段能力值/情绪强度
    section_groove: Optional[GrooveIntent] = None
    # 乐段和弦进行，建议强约束为 list，避免 LLM 输出随意字符串
    chord_progression: List[str] = Field(..., description="e.g. ['Cm', 'Ab', 'Bb', 'Gm']")

    # 每段每轨用哪个变体（编排核心）
    arrangement: Dict[str, VariantTag] = Field(
        default_factory=dict,
        description="e.g. {'drums':'light','bass':'mute','chords':'pad'...}"
    )

    # 各轨道的 “存在感” 权重（指导渲染力度 / 密度）
    presence: Dict[str, float] = Field(
        default_factory=dict,
        description="0-1, used by renderer to scale velocity/density"
    )
# 歌曲总定义
class SongBlueprint(BaseModel):
    song_name: str #歌曲名字
    bpm: int = Field(..., ge=60, le=200) #歌曲曲速
    scale: ScaleType = "minor" #调性
    root_note: str = Field("C", description="C, D#, F...") #根音
    total_bars: int = Field(..., ge=4) #总小节数
    # 风格描述
    style_description: str = Field(..., description="style + mood + reference hints")
    # 全局律动意图
    groove_global: GrooveIntent = Field(default_factory=GrooveIntent)
    # 歌曲分段计划
    sections: List[SectionPlan] = Field(default_factory=list)
    # 全局约束条件
    global_constraints: List[str] = Field(default_factory=list)

    @field_validator('sections')
    @classmethod
    def check_sections(cls, sections: List[SectionPlan]):
        if not sections:
            raise ValueError("必须定义歌曲结构 sections")
        return sections

# 8) 最终落盘结构：全曲导出
# -----------------------------
class ArrangementSchema(BaseModel):
    blueprint: SongBlueprint
    tracks: List[TrackSchema] = Field(default_factory=list)