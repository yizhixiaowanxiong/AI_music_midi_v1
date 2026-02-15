from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScaleDefinition(BaseModel):
    """调式定义。"""

    root: str = Field(..., description="根音，如 C/F#/Bb")
    name: str = Field(..., description="调式名称，如 Major/Minor/Dorian")
    intervals: Optional[List[int]] = Field(
        None,
        description="半音音程列表，用于自定义或生僻调式",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, value):
        if isinstance(value, dict):
            out = dict(value)
            if not out.get("name"):
                alias = out.get("type") or out.get("mode")
                if alias:
                    out["name"] = alias
            return out
        return value

    def get_intervals(self) -> List[int]:
        standard_scales = {
            "major": [0, 2, 4, 5, 7, 9, 11],
            "minor": [0, 2, 3, 5, 7, 8, 10],
            "dorian": [0, 2, 3, 5, 7, 9, 10],
            "phrygian": [0, 1, 3, 5, 7, 8, 10],
            "lydian": [0, 2, 4, 6, 7, 9, 11],
            "mixolydian": [0, 2, 4, 5, 7, 9, 10],
            "locrian": [0, 1, 3, 5, 6, 8, 10],
            "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
            "blues": [0, 3, 5, 6, 7, 10],
        }
        if self.intervals:
            return self.intervals
        return standard_scales.get(self.name.lower(), standard_scales["minor"])


class AgentRoutingRole(str, Enum):
    """Agent 路由角色定义。"""

    PERCUSSION = "percussion"
    BASS = "bass"
    HARMONY = "harmony"
    MELODY = "melody"
    FX = "fx"


class NoteEvent(BaseModel):
    """全系统统一音符结构。"""

    pitch: int = Field(..., ge=0, le=127)
    start_tick: int = Field(..., ge=0, description="相对 Section 起点 tick")
    duration_tick: int = Field(..., ge=1)
    velocity: int = Field(90, ge=1, le=127)
    tag: Optional[str] = Field(default=None, description="可选语义标签")


class GrooveIntent(BaseModel):
    """全局律动意图占位模型（当前渲染链路未消费）。"""

    # 保留模型入口并允许旧字段透传，避免历史请求直接报错。
    model_config = ConfigDict(extra="ignore")
