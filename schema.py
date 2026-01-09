from pydantic import BaseModel,Field
from typing import List,Optional,Literal

# 1.Literal类似一种枚举常量，强制值必须是其中之一
InstrumentType = Literal["piano", "bass", "drum", "synth_lead", "strings"]
ScaleType = Literal["major", "minor", "dorian", "phrygian"]

# 2.定义音符结构
class NoteSchema(BaseModel):
    """
        描述单个音符的各项参数
    """
    pitch:int=Field(...,description="MIDI 音高 (0-127), 例如 C4 是 60",ge=0,le=127)
    duration: int = Field(..., description="持续时间 (ticks), 假设 480 ticks 为一拍", ge=1)
    velocity: int = Field(90, description="力度 (0-127), 默认 90", ge=0, le=127)
    start_time: int = Field(..., description="相对于当前小节开始的绝对时间 (ticks)", ge=0)
# 3.定义自动化控制
class AutomationSchema(BaseModel):
    type: Literal["cutoff", "resonance", "volume"] = Field(..., description="控制参数类型")
    start_val: int = Field(0, ge=0, le=127)
    end_val: int = Field(127, ge=0, le=127)
    curve: Literal["linear", "exponential"] = "linear"
# 4.定义轨道结构
class TrackSchema(BaseModel):
    """
        描述一条完整的音轨
    """
    name: str = Field(..., description="轨道名称, 如 'Bass Line'")
    instrument: InstrumentType = Field(..., description="使用的乐器类型")
    notes: List[NoteSchema] = Field(..., description="该轨道包含的所有音符列表")
    automation: Optional[AutomationSchema] = Field(None, description="可选的自动化控制参数")
# 5.定义整体编曲结构
class ArrangementSchema(BaseModel):
    """
        要求 LLM 返回的最终 JSON 结构
    """
    song_name: str = Field(..., description="生成的歌曲名称")
    bpm: int = Field(120, description="速度 Beats Per Minute", ge=60, le=200)
    scale: ScaleType = Field("minor", description="调式")
    root_note: str = Field("C", description="根音, 如 C, D#, F")
    tracks: List[TrackSchema] = Field(..., description="包含的所有轨道列表")


# if __name__ == "__main__":
#     import json
#
#     # 1. 打印生成的 JSON Schema (这就是我们要喂给 LLM 的"说明书")
#     # Pydantic 会自动把 Python 类转换成标准的 JSON Schema 格式
#     print("=== 发送给 LLM 的 Schema 定义 ===")
#     print(json.dumps(ArrangementSchema.model_json_schema(), indent=2))
#
#     # 2. 模拟一个 LLM 返回的 JSON 数据
#     mock_llm_response = {
#         "song_name": "Cyberpunk City",
#         "bpm": 140,
#         "scale": "phrygian",
#         "root_note": "E",
#         "tracks": [
#             {
#                 "name": "Bass",
#                 "instrument": "bass",
#                 "notes": [
#                     {"pitch": 40, "duration": 480, "velocity": 100, "start_time": 0},
#                     {"pitch": 40, "duration": 480, "velocity": 90, "start_time": 480}
#                 ]
#             }
#         ]
#     }
#
#     # 3. 尝试解析 (Unmarshal)
#     try:
#         arrangement = ArrangementSchema(**mock_llm_response)
#         print(f"\n=== 解析成功 ===")
#         print(f"歌曲: {arrangement.song_name}, BPM: {arrangement.bpm}")
#         print(f"轨道 1 音符数: {len(arrangement.tracks[0].notes)}")
#     except Exception as e:
#         print(f"解析失败: {e}")