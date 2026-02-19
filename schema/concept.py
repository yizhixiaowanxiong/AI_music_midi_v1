"""概念层（Concept）schema - 极速版
只包含最核心的元数据，没有任何嵌套结构。
"""
import re
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


def _unwrap_nested(data: Any, known_keys: set[str]) -> Any:
    """LLM 有时返回 {"song_concept": {...}} 这种多余嵌套，在此自动解包。"""
    if not isinstance(data, dict):
        return data
    # 如果顶层已经包含已知字段，直接返回
    if known_keys & set(data.keys()):
        return data
    # 只有一个键且值是 dict → 尝试解包
    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            return inner
    return data


class SongConcept(BaseModel):
    """歌曲概念：只包含全局元数据，不包含结构列表。"""

    # 1. 核心风格 (简短准确，用于指导后续 Agent)
    style_description: str = Field(..., description="风格核心描述，如 'Cyberpunk Industrial Techno'")
    mood: str = Field(..., description="情感氛围关键词，如 'Dark, Aggressive'")

    # 2. 音乐锚点 (扁平化，不要嵌套对象)
    bpm: int = Field(..., gt=40, lt=300, description="BPM速度")
    time_signature: str = Field("4/4", description="拍号")

    # 3. 调式 (拆分为两个简单字段，不要 ScaleDefinition 对象)
    key_root: str = Field(..., description="根音，如 'C', 'F#'")
    key_mode: str = Field(..., description="调式，如 'Minor', 'Dorian'")

    # 4. 时长预估 (用于后续规划总小节数)
    target_duration_sec: int = Field(..., description="目标时长(秒)")

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        return _unwrap_nested(data, {"style_description", "mood", "bpm", "key_root"})

    @field_validator("time_signature")
    @classmethod
    def validate_ts(cls, v):
        if not re.match(r"^\d+/\d+$", str(v)):
            return "4/4" # 容错兜底
        return v