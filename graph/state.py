# graph/state.py
from typing import TypedDict, Optional, Dict, Any, List, Annotated
import operator


# 定义一个辅助函数，用来合并字典，而不是覆盖
def merge_dicts(a: Dict, b: Dict) -> Dict:
    return {**a, **b}

# 总定义
class MusicState(TypedDict):
    # 用户请求，字符串类型
    user_request: str

    # 用 Dict 存 blueprint 数据
    blueprint: Optional[Dict[str, Any]]

    # 加上 Annotated 和 merge_dicts
    # 这样 Drums 返回 {"drums":...}，Bass 返回 {"bass":...} 时
    # LangGraph 会自动把它们拼在一起，而不是互相覆盖
    tracks: Annotated[Dict[str, Any], merge_dicts]

    # 显式存储关键信号，给下游 Bass/Chords 用，避免重复计算
    kick_onsets: Optional[List[int]]
    chord_progression: Optional[List[str]]

    # 你的设计很好，保留用于 Critic
    critique: Optional[Dict[str, Any]]
    round: int
    errors: Annotated[List[str], operator.add]  # 错误信息也建议用 append 模式