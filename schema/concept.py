import re
from typing import List

from pydantic import BaseModel, Field, field_validator

from schema.base import GrooveIntent, ScaleDefinition

_TIME_SIGNATURE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class SectionConcept(BaseModel):
    """概念段落：定义段落氛围与能量，不包含具体小节计算。"""

    name: str = Field(..., description="段落名称，如 Intro/Verse/Drop")
    vibe: str = Field(..., description="段落氛围描述")
    energy_curve: float = Field(..., ge=0.0, le=1.0, description="能量等级，范围 [0.0, 1.0]")
    reference_tags: List[str] = Field(default_factory=list, description="风格参考标签")


class SongConcept(BaseModel):
    """歌曲概念：包含风格、速度、拍号、调式与结构草图。"""

    title: str
    style_description: str
    bpm: int = Field(..., gt=0, description="建议速度，必须为正整数")
    time_signature: str = Field(..., description="拍号，格式如 4/4、3/4、6/8")

    scale: ScaleDefinition
    global_groove: GrooveIntent
    structure_flow: List[SectionConcept]
    suggested_duration_range: str = Field(..., description="建议时长范围，如 180s-240s")

    @field_validator("time_signature")
    @classmethod
    def normalize_time_signature(cls, value: str) -> str:
        raw = str(value or "")
        match = _TIME_SIGNATURE_RE.match(raw)
        if not match:
            raise ValueError("time_signature must be in 'numerator/denominator' format")

        numerator = int(match.group(1))
        denominator = int(match.group(2))
        if numerator <= 0 or denominator <= 0:
            raise ValueError("time_signature numerator/denominator must be > 0")
        if denominator not in (1, 2, 4, 8, 16, 32):
            raise ValueError("time_signature denominator must be one of 1,2,4,8,16,32")

        return f"{numerator}/{denominator}"

    @field_validator("suggested_duration_range", mode="before")
    @classmethod
    def normalize_duration_range(cls, value):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return f"{value[0]}-{value[1]}"
        return str(value or "")
