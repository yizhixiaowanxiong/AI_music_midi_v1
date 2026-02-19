"""blueprint_schema.py - 极速骨架版"""
import re
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from schema.concept import SongConcept, _unwrap_nested

# --- 删掉 InstrumentDesign 相关的强约束 ---
# 或者保留它作为后续 Track 生成时的单独对象，但不放在 Blueprint 里

class SkeletonSection(BaseModel):
    """极简段落骨架：只定时间、和声、能量。"""
    model_config = ConfigDict(extra="ignore")

    index: int = Field(default=0)
    name: str = Field(..., description="段落名 (Intro/Verse/Chorus)")
    length_bars: int = Field(..., ge=1, description="段落长度(小节数)")
    chord_progression: List[str] = Field(..., description="和弦进行")
    energy_level: int = Field(..., ge=1, le=5, description="能量级")

    @field_validator("chord_progression", mode="before")
    @classmethod
    def _coerce_chord_progression(cls, v: Any) -> List[str]:
        """LLM 有时返回字符串而非数组，在此统一转为 List[str]。"""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return []
            # 按常见分隔符拆分: "Gm - Cm - Eb" / "Gm, Cm, Eb" / "Gm | Cm | Eb" / "Gm -> Cm -> Eb"
            parts = re.split(r"\s*(?:->|[,|;\-])\s*", text)
            return [p.strip() for p in parts if p.strip()]
        return [str(v)]


class SongBlueprint(BaseModel):
    """极速蓝图：只包含结构骨架。"""
    model_config = ConfigDict(extra="ignore")

    # 只要这两样东西，别的交给 Dispatcher 算
    user_confirmed_duration_sec: float = Field(..., description="用户确认的目标时长(秒)")
    sections: List[SkeletonSection]

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        return _unwrap_nested(data, {"sections", "user_confirmed_duration_sec"})