# note_schema.py
# 一个落盘的小demo：包含了drums，bass两个轨道
from pydantic import BaseModel, Field
from typing import List, Literal

# 音符定义
class NoteEvent(BaseModel):
    pitch: int = Field(..., ge=0, le=127) #音高
    start_tick: int = Field(..., ge=0) #开始时间
    duration_tick: int = Field(..., ge=1) #持续时间
    velocity: int = Field(90, ge=1, le=127) #力度

class TrackOut(BaseModel):
    name: str
    instrument: Literal["drums", "bass"]
    channel: int = Field(..., ge=0, le=15)
    pattern_length_tick: int = Field(..., ge=120)  # 乐段长度，以tick为单位：比如 8 bars = 8*1920=15360
    loop_count: int = Field(..., ge=1) #循环次数
    notes: List[NoteEvent] = Field(default_factory=list)

class DrumsOut(BaseModel):
    track: TrackOut

class BassOut(BaseModel):
    track: TrackOut
