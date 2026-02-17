"""基础音乐语义模型。

说明：
- 这些模型是全项目共享的最底层 schema。
- 尽量保持字段稳定，避免上层大量联动修改。
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScaleDefinition(BaseModel):
    """调式定义。"""

    root: str = Field(..., description="根音，例如 C / F# / Bb")
    name: str = Field(..., description="调式名，例如 Major / Minor / Dorian")
    intervals: Optional[List[int]] = Field(
        None,
        description="半音音程序列；为空时将按 name 使用内置标准调式。",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_keys(cls, value):
        """兼容历史字段：type/mode -> name。"""
        if isinstance(value, dict):
            out = dict(value)
            if not out.get("name"):
                alias = out.get("type") or out.get("mode")
                if alias:
                    out["name"] = alias
            return out
        return value

    def get_intervals(self) -> List[int]:
        """返回当前调式的半音音程。"""
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
    """轨道 Agent 的标准角色枚举。"""

    PERCUSSION = "percussion"
    BASS = "bass"
    HARMONY = "harmony"
    MELODY = "melody"
    FX = "fx"


class NoteEvent(BaseModel):
    """统一音符事件结构（项目内标准）。"""

    pitch: int = Field(..., ge=0, le=127)
    start_tick: int = Field(..., ge=0, description="相对当前段落起点的 tick")
    duration_tick: int = Field(..., ge=1)
    velocity: int = Field(90, ge=1, le=127)
    tag: Optional[str] = Field(default=None, description="可选语义标签")


class GrooveIntent(BaseModel):
    """全局律动意图占位模型。

    当前渲染链路未严格消费该结构，保持 extra=ignore 以兼容历史输入。
    """

    model_config = ConfigDict(extra="ignore")
