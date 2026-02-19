"""轨道执行器：负责 Agent 加载、上下文兜底注入、并发与超时控制。"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import Any, Dict, Mapping, Optional

from schema.request import GeneratedTrack, TrackContext, TrackGenRequest
from utils.context_tools import build_context_summary

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    """读取环境变量并做最小值保护。"""
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return max(min_value, int(default))
    try:
        value = int(raw)
    except ValueError:
        value = int(default)
    return max(min_value, value)


_CONCURRENCY = _env_int("TRACK_CONCURRENCY", 5, 1)
_TIMEOUT_SEC = float(_env_int("TRACK_TIMEOUT_SEC", 120, 1))
_SEM = asyncio.Semaphore(_CONCURRENCY)

AGENT_REGISTRY = {
    "drums": ("agents.drums_agent", "DrumsAgent"),
    "percussion": ("agents.drums_agent", "DrumsAgent"),
    "bass": ("agents.bass_agent", "BassAgent"),
    "melody": ("agents.melody_agent", "MelodyAgent"),
    "harmony": ("agents.harmony_agent", "HarmonyAgent"),
    "fx": ("agents.fx_agent", "FxAgent"),
}

_AGENT_CACHE: Dict[str, Any] = {}


def _normalize_role(role: Any) -> str:
    """统一角色文本，兼容 percussion/drums 命名差异。"""
    value = getattr(role, "value", role)
    text = str(value or "").strip().lower()
    if text == "percussion":
        return "drums"
    return text


def _cache_key(run_scope_id: str, role: str) -> str:
    """构造运行期缓存键（按 run 作用域隔离）。"""
    return f"{str(run_scope_id or 'default').strip()}:{str(role or '').strip()}"


def _get_agent_class(role: str):
    """动态加载 Agent 类，失败返回 None。"""
    role_key = _normalize_role(role)
    if role_key not in AGENT_REGISTRY:
        logger.warning("Unknown role '%s', no registered agent.", role)
        return None

    module_path, class_name = AGENT_REGISTRY[role_key]
    try:
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        logger.error("Failed to load agent for role '%s': %s", role_key, exc)
        return None


def get_agent_instance(role: str, run_scope_id: str = "default"):
    """获取（或创建）缓存 Agent 实例。"""
    role_key = _normalize_role(role)
    key = _cache_key(run_scope_id, role_key)
    cached = _AGENT_CACHE.get(key)
    if cached is not None:
        return cached

    cls = _get_agent_class(role_key)
    if cls is None:
        return None

    agent = cls()
    _AGENT_CACHE[key] = agent
    return agent


def clear_run_cache(run_scope_id: str) -> None:
    """清理指定 run 作用域下的 Agent 缓存。"""
    prefix = f"{str(run_scope_id or '').strip()}:"
    keys = [k for k in _AGENT_CACHE.keys() if k.startswith(prefix)]
    for key in keys:
        _AGENT_CACHE.pop(key, None)


def clear_run_runtime(run_scope_id: str | None) -> None:
    """对齐 graph 侧命名的兼容接口。"""
    clear_run_cache(str(run_scope_id or ""))


def _context_to_prev_tracks(runtime_context: Any) -> Dict[str, Any]:
    """仅当 runtime_context 是映射时，作为 prev_tracks 使用。"""
    if isinstance(runtime_context, Mapping):
        return dict(runtime_context)
    return {}


def _ensure_request_context(req: TrackGenRequest, runtime_context: Any) -> None:
    """把 TrackContext 注入到 req.context（如果请求里尚未携带）。"""
    if getattr(req, "context", None):
        return
    if isinstance(runtime_context, TrackContext):
        req.context = runtime_context


async def run_one_track(
    req: TrackGenRequest,
    runtime_context: Any = None,
    run_scope_id: str | None = None,
    session_scope_id: str | None = None,
    run_id: str | None = None,
) -> GeneratedTrack:
    """
    运行单条轨道任务（兼容 graph 调用）：
    1) 规范 role/instrument
    2) 兜底注入 context_summary
    3) 加载 Agent 并执行
    """
    _ = session_scope_id
    scope = str(run_scope_id or run_id or "default")

    role_value = getattr(req, "instrument", None) or getattr(req, "role", None)
    role_name = _normalize_role(role_value)
    if not role_name:
        return _create_error_track(req, "missing role/instrument")

    if not str(getattr(req, "role", "") or "").strip():
        req.role = str(role_name)
    if not str(getattr(req, "instrument", "") or "").strip():
        req.instrument = str(role_name)

    _ensure_request_context(req, runtime_context)
    # 只有上游没注入时才做兜底摘要，避免重复计算与提示漂移。
    if not str(getattr(req, "context_summary", "") or "").strip():
        req.context_summary = build_context_summary(req, _context_to_prev_tracks(runtime_context))

    agent = get_agent_instance(role_name, scope)
    if agent is None:
        return _create_error_track(req, f"No agent for role={role_name}")

    try:
        async with _SEM:
            logger.info("[%s] Start generating section %s...", role_name, req.section_index)
            out = await asyncio.wait_for(agent.generate(req), timeout=_TIMEOUT_SEC)
            if isinstance(out, GeneratedTrack):
                return out
            return GeneratedTrack(
                track_key=str(getattr(req, "track_key", "") or "unknown"),
                instrument=getattr(req, "instrument", role_name),
                section_name=str(getattr(req, "section_name", "") or "Unknown"),
                notes=[],
                raw_output=out,
            )
    except asyncio.TimeoutError:
        return _create_error_track(req, f"Timeout after {_TIMEOUT_SEC:.1f}s")
    except Exception as exc:
        logger.exception("[%s] Runner failed: %s", role_name, exc)
        return _create_error_track(req, str(exc))


def _create_error_track(req: TrackGenRequest, error_msg: str) -> GeneratedTrack:
    """统一错误兜底输出，保证上游流程不中断。"""
    return GeneratedTrack(
        track_key=str(getattr(req, "track_key", "") or "unknown"),
        instrument=getattr(req, "instrument", "") or getattr(req, "role", "") or "unknown",
        section_name=str(getattr(req, "section_name", "") or "Unknown"),
        notes=[],
        error=str(error_msg or "runner_error"),
    )
