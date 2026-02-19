# schema/base.py

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

class NoteEvent(BaseModel):
    """最基础的音符事件。"""
    model_config = ConfigDict(extra="ignore")
    pitch: int = Field(..., ge=0, le=127)
    start_tick: int = Field(..., ge=0)
    duration_tick: int = Field(default=480, gt=0)  # 默认一个四分音符（480 ticks）
    velocity: int = Field(default=100, ge=0, le=127)

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        """LLM 可能省略 duration_tick 或返回 0/负数，统一兜底。"""
        if not isinstance(data, dict):
            return data
        dt = data.get("duration_tick")
        if dt is None or (isinstance(dt, (int, float)) and dt <= 0):
            data["duration_tick"] = 480
        return data

class AgentRoutingRole(str, Enum):
    BASS = "bass"
    MELODY = "melody"
    HARMONY = "harmony"
    PERCUSSION = "percussion"
    FX = "fx"