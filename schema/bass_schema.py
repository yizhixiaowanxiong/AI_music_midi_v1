# schema/bass_schema.py
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional, Set

# 变体，乐段名，约束性
VariantTag = Literal["mute", "core", "light", "full", "build", "break", "fill"]
SectionName = Literal["Intro", "Verse", "Build-up", "Drop", "Breakdown", "Outro"]
Strictness = Literal[0, 1, 2]
# 音符定义
class BassNote(BaseModel):
    pitch: int = Field(..., ge=0, le=127)      # MIDI note
    start_tick: int = Field(..., ge=0)         # relative to pattern start
    duration_tick: int = Field(..., ge=1)
    velocity: int = Field(90, ge=1, le=127)
# 变体类型
class BassPattern(BaseModel):
    tag: VariantTag = "core"
    notes: List[BassNote] = Field(default_factory=list)
# 乐段计划
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
# 输出
class BassSectionOutput(BaseModel):
    track_key: Literal["bass"] = "bass"
    section_name: SectionName
    section_start_bar: int = Field(..., ge=1)
    section_end_bar: int = Field(..., ge=1)

    time_signature: str = Field("4/4")
    ticks_per_beat: int = 480
    bar_ticks: int = Field(0, ge=1)
    strictness: Strictness = 1

    pattern_len_bars: Literal[1, 2, 4, 8] = 4
    patterns: List[BassPattern] = Field(default_factory=list)
    phrases: List[PhrasePlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def qc(self):
        if self.section_end_bar < self.section_start_bar:
            raise ValueError("section_end_bar must be >= section_start_bar")

        if self.bar_ticks == 0:
            n, d = self.time_signature.split("/")
            self.bar_ticks = int(self.ticks_per_beat * int(n) * (4 / int(d)))

        section_len = self.section_end_bar - self.section_start_bar + 1
        pattern_ticks = self.pattern_len_bars * self.bar_ticks

        if not self.phrases:
            raise ValueError("phrases must not be empty")

        cover = [0] * section_len
        for ph in self.phrases:
            if ph.start_bar < self.section_start_bar or ph.end_bar > self.section_end_bar:
                raise ValueError("phrase bars must be inside section range")
            for b in range(ph.start_bar, ph.end_bar + 1):
                cover[b - self.section_start_bar] += 1
        if any(c == 0 for c in cover):
            raise ValueError("phrases do not fully cover the section")
        if any(c > 1 for c in cover):
            raise ValueError("phrases overlap")

        tags: Set[str] = {p.tag for p in self.patterns}
        for ph in self.phrases:
            if ph.use_pattern_tag not in tags:
                raise ValueError(f"use_pattern_tag={ph.use_pattern_tag} not in {tags}")
            if ph.end_fill_tag and ph.end_fill_tag not in tags:
                raise ValueError(f"end_fill_tag={ph.end_fill_tag} not in {tags}")

        for p in self.patterns:
            for n in p.notes:
                if not (0 <= n.start_tick < pattern_ticks):
                    raise ValueError("note.start_tick out of range")
                if n.start_tick + n.duration_tick > pattern_ticks:
                    raise ValueError("note overflows pattern")
        return self
