import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept


def merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    return {**(a or {}), **(b or {})}


class MusicState(TypedDict):
    # 运行唯一标识。MCP/LangGraph 都通过 run_id(thread_id) 做状态恢复与查询。
    run_id: str
    # 状态机阶段：
    # concept_draft -> waiting_review -> blueprint_build -> section_generate -> done/failed
    phase: str  # concept_draft|waiting_review|blueprint_build|section_generate|done|failed

    # 用户原始需求。
    user_request: str
    # concept 审核输入：
    # {"approved": bool, "feedback": str|None}
    concept_review: Optional[Dict[str, Any]]
    # 用户确认时长（秒）。通过审核后进入 blueprint 计算时使用。
    user_confirmed_duration_sec: Optional[float]

    # 规划阶段产物：概念与总蓝图。
    concept: Optional[SongConcept]
    blueprint: Optional[SongBlueprint]

    # 生成阶段参数与产物。
    # strictness: 生成保守度；tracks: 分段轨道结果；result_payload: 可观测统计信息。
    strictness: int
    tracks: Annotated[Dict[str, Any], merge_dicts]
    result_payload: Annotated[Dict[str, Any], merge_dicts]

    # 对外错误面：MCP/前端轮询时读取。
    last_error: Optional[str]
    last_error_code: Optional[str]
    # 历史错误列表（reducer 追加），便于排查一次运行内的多条问题。
    errors: Annotated[List[str], operator.add]
