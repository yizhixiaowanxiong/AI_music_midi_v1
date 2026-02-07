# blueprint_schema.py
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Dict


# 调式和乐段名称枚举
ScaleType = Literal["major", "minor", "dorian", "phrygian", "lydian"]
SectionName = Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"]

# 轨道角色枚举和乐器变体标签
TrackKey = Literal["drums", "bass", "chords", "pad", "lead", "fx", "vocal"]
VariantTag = Literal["mute", "core", "light", "full", "build", "break", "fill"]

# Section内部的变体逻辑，段落内“乐句/分片”
class PhrasePlan(BaseModel):
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    # 只写“覆盖哪些轨道用什么变体”，不写 InstrumentInstruction（保持轻量）
    arrangement_override: Dict[TrackKey, VariantTag] = Field(default_factory=dict)
    @field_validator("end_bar")
    @classmethod
    def _end_ge_start(cls, v, info):
        if "start_bar" in info.data and v < info.data["start_bar"]:
            raise ValueError("PhrasePlan.end_bar must be >= start_bar")
        return v

# Groove：意图层 (Renderer 使用)
# 数值映射交给 Renderer（稳定、可控）
class GrooveIntent(BaseModel):
    humanize_level: Literal["off", "low", "mid", "high"] = "mid" #人性化水平
    swing: Literal["off", "low", "mid", "high"] = "mid" #摆动
    feel: Literal["tight", "neutral", "laid_back"] = "neutral" #节奏性
# 乐器命令
class InstrumentInstruction(BaseModel):
    role: Literal["silent", "background", "support", "lead", "solo"] = Field(..., description="乐器角色")  #乐器地位
    playing_style: str = Field(..., description="演奏风格建议（文字）") #演奏风格
    energy_level: float = Field(..., ge=0.0, le=1.0, description="该乐器在该段的能量/强度") #强度
    variant_tag: VariantTag = Field("../core", description="建议使用的变体标签") #变体标签，默认初始核心
# 乐段定义
class Section(BaseModel):
    name: SectionName #小节名字
    start_bar: int = Field(..., ge=1) #开始节数
    end_bar: int = Field(..., ge=1) #结束节数
    global_energy: float = Field(..., ge=0.0, le=1.0) #整体能量控制
    # 和弦进行
    chord_progression: List[str] = Field(..., min_length=1, description="如 ['Cm','Ab','Bb','Gm']")
    # 加一个“和弦变化粒度”避免歧义
    chord_rhythm: Literal["1bar", "2bar", "4bar"] = "4bar"
    # 内部循环
    progression_is_loop: bool = True  # ✅新增：强制定义语义
    repeat: int = Field(1, ge=1)  # ✅新增：循环次数

    arrangement: Dict[TrackKey, InstrumentInstruction] = Field(
        ..., description="各乐器编排指令（key 必须是固定枚举）"
    )
    # 乐段内部和弦变化
    phrases: List[PhrasePlan] = Field(default_factory=list)

    # 开始-结束约束
    @field_validator("end_bar")
    @classmethod
    def _end_ge_start(cls, v, info):
        start = info.data.get("start_bar")
        if start is not None and v < start:
            raise ValueError("end_bar must be >= start_bar")
        return v
    # 填充Section
    @model_validator(mode="after")
    def check_chords_fit_section(self):
        # 当前段落小节数
        section_bars = self.end_bar - self.start_bar + 1
        # 每个和弦占的小节数
        bars_per_chord = {"1bar": 1, "2bar": 2, "4bar": 4}[self.chord_rhythm]
        # 和弦进行循环一次的总小节数
        loop_bars = len(self.chord_progression) * bars_per_chord
        # 和弦进行域段落小节数匹配校验
        if self.progression_is_loop:
            if section_bars != loop_bars * self.repeat:
                raise ValueError(
                    f"{self.name}: section_bars={section_bars} 但 loop_bars={loop_bars} * repeat={self.repeat} 不匹配"
                )
        else:
            # 另一种语义：progression 直接覆盖整段（不循环）
            if section_bars != loop_bars:
                raise ValueError(
                    f"{self.name}: section_bars={section_bars} 但 progression覆盖={loop_bars} 不匹配"
                )

        if section_bars >= 16 and not self.phrases:
            raise ValueError(f"{self.name}: section_len>=16 建议必须提供 phrases 以产生变化")
        # 大于 16 小节数的乐段必须提供合法校验
        if self.phrases:
            # 用 cover 数组记录每小节被乐句覆盖的次数
            cover = [0] * section_bars
            for p in self.phrases:
                # phrases 必须落在 section 内
                if p.start_bar < self.start_bar or p.end_bar > self.end_bar:
                    raise ValueError(f"{self.name}: phrase out of section range")
                for b in range(p.start_bar, p.end_bar + 1):
                    cover[b - self.start_bar] += 1
            # 不允许有小节未被覆盖（避免段落 “留白”）
            if any(c == 0 for c in cover):
                raise ValueError(f"{self.name}: phrases 未覆盖 section 全部小节")
            # 不允许有小节被多个乐句重叠覆盖
            if any(c > 1 for c in cover):
                raise ValueError(f"{self.name}: phrases 存在重叠覆盖")
        return self

# 歌曲总谱
class SongBlueprint(BaseModel):
    song_name: str
    style_description: str #风格

    bpm: int = Field(..., ge=60, le=200)
    time_signature: str = Field("4/4") #节拍
    root_note: str = Field("C", description="如 C, D#, F") #根音
    scale: ScaleType = "minor" #类型
    # 总小节数
    total_bars: int = Field(..., ge=4)
    groove_global: GrooveIntent = Field(default_factory=GrooveIntent) #全局意图层定义
    # 乐段长度
    sections: List[Section] = Field(..., min_length=1)
    global_constraints: List[str] = Field(default_factory=list) #全局约束

    @model_validator(mode="after")
    def _qc_song_structure(self):
        """
        QC 规则：
        1) 段落覆盖 1..total_bars，且不重叠
        2) 必须有 Drop，且 Drop 能量为全曲最高（或并列最高）
        3) 每段必须包含至少 drums/pad/chords/bass 中的一些 key（允许 mute）
        """
        # 1) 小节覆盖重叠判断
        # 初始化长度为总小节数的列表，每个元素代表对应小节的被覆盖次数，初始为0
        cover = [0] * self.total_bars
        # 遍历每个小节
        for s in self.sections:
            for b in range(s.start_bar, s.end_bar + 1):
                if 1 <= b <= self.total_bars:
                    cover[b - 1] += 1
        if any(c == 0 for c in cover):
            raise ValueError("sections 未覆盖全曲（存在未覆盖小节）")
        if any(c > 1 for c in cover):
            raise ValueError("sections 存在重叠（同一小节被多个段覆盖）")

        # 2) Drop包含及能量判断
        drops = [s for s in self.sections if s.name == "Drop"]
        if not drops:
            raise ValueError("必须包含 Drop 段")
        max_energy = max(s.global_energy for s in self.sections)
        if drops[0].global_energy < max_energy:
            raise ValueError("Drop 段 global_energy 必须为全曲最高（或并列最高）")

        # 3) 音轨乐器类别包含判断
        required = {"drums", "bass", "chords", "pad"}
        for s in self.sections:
            keys = set(s.arrangement.keys())
            if len(keys & required) < 2:
                raise ValueError(f"{s.name} 段 arrangement 过少：建议至少包含 {required} 中的两类")

        return self
