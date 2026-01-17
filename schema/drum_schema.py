# drums_schema.py (UPGRADED)
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional, Set

# 变体和章节名字定义----和blueprint 对齐
VariantTag = Literal["mute", "core", "light", "full", "build", "break", "fill"]

SectionName = Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"]

# 软约束旋钮（0 发散 / 1 稳定 / 2 工业模板更强）
Strictness = Literal[0, 1, 2]


# 鼓点音符
class DrumNote(BaseModel):
    pitch: int = Field(..., ge=0, le=127)          # GM drum note number
    start_tick: int = Field(..., ge=0)             # relative to pattern start
    duration_tick: int = Field(..., ge=1)
    velocity: int = Field(90, ge=1, le=127)

# 鼓点填充
class DrumPattern(BaseModel):
    tag: VariantTag = "core"
    notes: List[DrumNote] = Field(default_factory=list)

# 乐段规划
class PhrasePlan(BaseModel):
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    use_pattern_tag: VariantTag = "core"
    end_fill_tag: Optional[VariantTag] = None

    @field_validator("end_bar")
    @classmethod
    def end_ge_start(cls, v, info):
        if "start_bar" in info.data and v < info.data["start_bar"]:
            raise ValueError("end_bar must be >= start_bar")
        return v


# 章节输出
class DrumsSectionOutput(BaseModel):
    track_key: Literal["drums"] = "drums"
    section_name: SectionName
    section_start_bar: int = Field(..., ge=1)
    section_end_bar: int = Field(..., ge=1)

    # CHANGED: meter/timebase 从 blueprint 传进来，而不是写死
    time_signature: str = Field("4/4", description="e.g. '4/4', '3/4', '6/8'")
    ticks_per_beat: int = Field(480, ge=24, le=1920, description="PPQ/TPB")
    bar_ticks: int = Field(0, ge=1, description="computed if 0")

    strictness: Strictness = 1

    # CHANGED: 放宽 pattern 长度，不要把灵感锁死在 4/8
    pattern_len_bars: Literal[1, 2, 4, 8] = 4

    patterns: List[DrumPattern] = Field(default_factory=list)
    phrases: List[PhrasePlan] = Field(default_factory=list)

    # QC / validators (Hard constraints)
    @model_validator(mode="after")
    def qc(self):
        # 1) 段落边界合法性校验
        if self.section_end_bar < self.section_start_bar:
            raise ValueError("section_end_bar must be >= section_start_bar")
            # CHANGED: 自动计算 bar_ticks（避免 4/4 only）
        if self.bar_ticks == 0:
            # time_signature "n/d": n beats per bar, beat unit = d
            n, d = self.time_signature.split("/")
            beats_per_bar = int(n)
            beat_unit = int(d)
            # ticks_per_beat is ticks per quarter-note -> scale for beat unit
            self.bar_ticks = int(self.ticks_per_beat * beats_per_bar * (4 / beat_unit))

        # 得到段落总小节数和单个pattern的总小节数
        section_len = self.section_end_bar - self.section_start_bar + 1
        pattern_ticks = self.pattern_len_bars * self.bar_ticks

        # 2) 音符时间范围校验
        for p in self.patterns:
            for n in p.notes:
                if not (0 <= n.start_tick < pattern_ticks):
                    raise ValueError(
                        f"note.start_tick out of range for tag={p.tag}: "
                        f"{n.start_tick} not in [0, {pattern_ticks})"
                    )
                if n.start_tick + n.duration_tick > pattern_ticks:
                    raise ValueError(
                        f"note overflows pattern for tag={p.tag}: "
                        f"start+dur={n.start_tick+n.duration_tick} > {pattern_ticks}"
                    )

        # 3) 乐句（phrases）时间覆盖完整性校验
        if not self.phrases:
            raise ValueError("phrases must not be empty (need arrangement over time)")

        cover = [0] * section_len
        for ph in self.phrases:
            # 章节内乐段的规划开始结束时间要在章节内
            if ph.start_bar < self.section_start_bar or ph.end_bar > self.section_end_bar:
                raise ValueError("phrase bars must be inside section range")
            # 所有乐句必须完全覆盖整个段落，统计每小节被覆盖的次数
            for b in range(ph.start_bar, ph.end_bar + 1):
                idx = b - self.section_start_bar
                cover[idx] += 1
        # 乐句之间不能重叠
        if any(c == 0 for c in cover):
            raise ValueError("phrases do not fully cover the section (missing bars)")
        if any(c > 1 for c in cover):
            raise ValueError("phrases overlap (a bar is covered more than once)")

        # 4) 乐句关联的Patter标签合法性校验
        tags: Set[str] = {p.tag for p in self.patterns} #收集标签集合
        for ph in self.phrases:
            if ph.use_pattern_tag not in tags:
                raise ValueError(f"phrase use_pattern_tag={ph.use_pattern_tag} not in patterns tags={tags}")
            if ph.end_fill_tag and ph.end_fill_tag not in tags:
                raise ValueError(f"phrase end_fill_tag={ph.end_fill_tag} not in patterns tags={tags}")

        return self
