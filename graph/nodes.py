import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from schema.arrangement import GeneratedTrack, TrackContext, TrackGenRequest
from schema.base import AgentRoutingRole
from schema.blueprint_schema import SongBlueprint
from utils.context_tools import (
    extract_harmony_summary,
    extract_kick_summary,
    extract_last_bar_midi,
    extract_melody_summary,
)
from utils.channel_allocator import MidiChannelAllocator
from utils.dispatch import dispatch_section_to_requests
from utils.runner import run_one_track

RequestKey = Tuple[int, str]  # (section_index, track_key)


def _section_key(section_idx: int, section: Any) -> str:
    return f"{section_idx}:{section.name}"


def _same_section_dependencies(role: AgentRoutingRole) -> Tuple[AgentRoutingRole, ...]:
    if role == AgentRoutingRole.BASS:
        return (AgentRoutingRole.PERCUSSION,)
    if role == AgentRoutingRole.MELODY:
        return (AgentRoutingRole.HARMONY,)
    if role == AgentRoutingRole.FX:
        return (AgentRoutingRole.MELODY,)
    return ()


def _should_pass_last_midi(
    section_idx: int,
    total_sections: int,
    role_keys_by_section: Dict[int, Dict[AgentRoutingRole, List[RequestKey]]],
) -> bool:
    if section_idx + 1 >= total_sections:
        return False

    next_roles = role_keys_by_section.get(section_idx + 1, {})
    next_needs_transition = bool(
        next_roles.get(AgentRoutingRole.HARMONY) or next_roles.get(AgentRoutingRole.MELODY)
    )
    if not next_needs_transition:
        return False

    current_roles = role_keys_by_section.get(section_idx, {})
    has_transition_source = bool(
        current_roles.get(AgentRoutingRole.HARMONY) or current_roles.get(AgentRoutingRole.MELODY)
    )
    return has_transition_source


def _build_dependency_graph(
    sections: List[Any],
    role_keys_by_section: Dict[int, Dict[AgentRoutingRole, List[RequestKey]]],
    req_map: Dict[RequestKey, TrackGenRequest],
) -> Dict[RequestKey, Set[RequestKey]]:
    deps: Dict[RequestKey, Set[RequestKey]] = {}
    total_sections = len(sections)

    for req_key, req in req_map.items():
        section_idx, _ = req_key
        role = getattr(req, "instrument", None)
        cur: Set[RequestKey] = set()

        for dep_role in _same_section_dependencies(role):
            cur.update(role_keys_by_section.get(section_idx, {}).get(dep_role, []))

        if section_idx > 0 and role in (AgentRoutingRole.HARMONY, AgentRoutingRole.MELODY):
            prev_idx = section_idx - 1
            if _should_pass_last_midi(prev_idx, total_sections, role_keys_by_section):
                prev_roles = role_keys_by_section.get(prev_idx, {})
                cur.update(prev_roles.get(AgentRoutingRole.HARMONY, []))
                cur.update(prev_roles.get(AgentRoutingRole.MELODY, []))

        cur.discard(req_key)
        deps[req_key] = cur

    return deps


def _merge_unique_ints(left: List[int], right: List[int]) -> List[int]:
    merged = set()
    for value in (left or []):
        try:
            merged.add(int(value))
        except Exception:
            continue
    for value in (right or []):
        try:
            merged.add(int(value))
        except Exception:
            continue
    return sorted(merged)


def _merge_optional_text(current: Any, incoming: Any, sep: str = " | ") -> Any:
    cur = str(current or "").strip()
    nxt = str(incoming or "").strip()
    if not nxt:
        return cur or None
    if not cur:
        return nxt

    parts = [part.strip() for part in cur.split(sep) if part.strip()]
    if nxt in parts:
        return sep.join(parts)
    parts.append(nxt)
    return sep.join(parts)


def _merge_chord_notes_per_bar(
    current: List[List[int]],
    incoming: List[List[int]],
) -> List[List[int]]:
    left = current or []
    right = incoming or []
    max_len = max(len(left), len(right))
    out: List[List[int]] = []
    for i in range(max_len):
        left_notes = left[i] if i < len(left) else []
        right_notes = right[i] if i < len(right) else []
        out.append(_merge_unique_ints(left_notes, right_notes))
    return out


def _track_key_of_transition_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("track_key", "") or "")
    return str(getattr(item, "track_key", "") or "")


def _merge_transition_bundle(current: List[Any], incoming: List[Any]) -> List[Any]:
    merged: Dict[str, Any] = {}
    ordered_keys: List[str] = []
    for item in (current or []):
        key = _track_key_of_transition_item(item)
        if key and key not in merged:
            ordered_keys.append(key)
        if key:
            merged[key] = item
    for item in (incoming or []):
        key = _track_key_of_transition_item(item)
        if key and key not in merged:
            ordered_keys.append(key)
        if key:
            merged[key] = item
    return [merged[key] for key in ordered_keys if key in merged]


def _update_runtime_context(ctx: TrackContext, track: GeneratedTrack) -> None:
    role = getattr(track, "instrument", None)
    if role == AgentRoutingRole.PERCUSSION:
        summary = extract_kick_summary(track)
        if summary:
            ctx.kick_onsets_ticks = _merge_unique_ints(
                list(ctx.kick_onsets_ticks or []),
                list(summary.get("kick_onsets_ticks", []) or []),
            )
            ctx.kick_summary_text = _merge_optional_text(
                ctx.kick_summary_text,
                summary.get("kick_summary_text"),
                sep=" | ",
            )
            ctx.break_ranges = _merge_optional_text(
                ctx.break_ranges,
                summary.get("break_ranges"),
                sep=",",
            )
            ctx.kick_pitches = _merge_unique_ints(
                list(ctx.kick_pitches or []),
                list(summary.get("kick_pitches") or []),
            )
    elif role == AgentRoutingRole.HARMONY:
        summary = extract_harmony_summary(track)
        if summary:
            ctx.chord_notes_per_bar = _merge_chord_notes_per_bar(
                list(ctx.chord_notes_per_bar or []),
                list(summary.get("chord_notes_per_bar", []) or []),
            )
    elif role == AgentRoutingRole.MELODY:
        summary = extract_melody_summary(track)
        if summary:
            ctx.lyric_rhythm_ticks = _merge_unique_ints(
                list(ctx.lyric_rhythm_ticks or []),
                list(summary.get("lyric_rhythm_ticks", []) or []),
            )


def _update_transition_context(
    section_idx: int,
    total_sections: int,
    track: GeneratedTrack,
    section_contexts: Dict[int, TrackContext],
    role_keys_by_section: Dict[int, Dict[AgentRoutingRole, List[RequestKey]]],
) -> None:
    if not _should_pass_last_midi(section_idx, total_sections, role_keys_by_section):
        return

    if getattr(track, "instrument", None) not in (AgentRoutingRole.MELODY, AgentRoutingRole.HARMONY):
        return

    item = extract_last_bar_midi(track, max_notes=10, grid_div=4)
    if not item:
        return

    next_ctx = section_contexts.setdefault(section_idx + 1, TrackContext())
    next_ctx.prev_section_last_bar_midi = _merge_transition_bundle(
        list(next_ctx.prev_section_last_bar_midi or []),
        [item],
    )


async def section_generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    # 主流程：按 section 生成请求，按依赖拓扑并发执行，并持续更新运行态上下文。
    blueprint = state.get("blueprint")
    if blueprint is None:
        raise ValueError("missing_blueprint: state.blueprint is required for section generation.")
    if not isinstance(blueprint, SongBlueprint):
        raise TypeError("invalid_blueprint: state.blueprint must be SongBlueprint.")

    strictness = int(state.get("strictness", 1) or 1)
    sections = list(blueprint.sections)
    total_sections = len(sections)

    tracks_by_section_idx: Dict[int, List[GeneratedTrack]] = defaultdict(list)
    section_contexts: Dict[int, TrackContext] = {idx: TrackContext() for idx in range(total_sections)}
    section_locks: Dict[int, asyncio.Lock] = {idx: asyncio.Lock() for idx in range(total_sections)}
    run_errors: List[str] = []
    channel_allocator = MidiChannelAllocator()

    req_map: Dict[RequestKey, TrackGenRequest] = {}
    role_keys_by_section: Dict[int, Dict[AgentRoutingRole, List[RequestKey]]] = defaultdict(
        lambda: defaultdict(list)
    )
    section_name_by_idx: Dict[int, str] = {}

    for section_idx, section in enumerate(sections):
        section_name_by_idx[section_idx] = _section_key(section_idx, section)
        requests = dispatch_section_to_requests(
            section=section,
            blueprint=blueprint,
            section_runtime_index=section_idx,
            strictness=strictness,
        )
        for req in requests:
            req.midi_channel = channel_allocator.assign(req.track_key, req.instrument)
            key: RequestKey = (section_idx, req.track_key)
            req_map[key] = req
            role_keys_by_section[section_idx][req.instrument].append(key)

    if not req_map:
        return {"tracks": {}, "errors": []}

    deps = _build_dependency_graph(sections, role_keys_by_section, req_map)
    pending: Set[RequestKey] = set(req_map.keys())
    completed: Set[RequestKey] = set()

    async def _run_request(req_key: RequestKey) -> None:
        section_idx, _ = req_key
        req = req_map[req_key]
        ctx = section_contexts.setdefault(section_idx, TrackContext())
        try:
            track = await run_one_track(req, ctx)
            async with section_locks.setdefault(section_idx, asyncio.Lock()):
                tracks_by_section_idx[section_idx].append(track)

                # run_one_track 可能返回带 error 的轨道对象，这里统一汇总到 run_errors。
                if getattr(track, "error", None):
                    run_errors.append(
                        f"section={section_idx} track={getattr(req, 'track_key', 'unknown')}: "
                        f"{str(track.error)}"
                    )

                _update_runtime_context(ctx, track)
                _update_transition_context(
                    section_idx=section_idx,
                    total_sections=total_sections,
                    track=track,
                    section_contexts=section_contexts,
                    role_keys_by_section=role_keys_by_section,
                )
        except Exception as exc:
            # 防御性兜底：任务异常也记录到 run_errors。
            run_errors.append(
                f"section={section_idx} track={getattr(req, 'track_key', 'unknown')}: {str(exc)}"
            )

    while pending:
        # 拓扑调度：优先执行依赖已满足的任务；若无可执行节点则判定依赖死锁并终止调度。
        ready = [key for key in pending if deps.get(key, set()) <= completed]
        if not ready:
            details: List[str] = []
            for req_key in sorted(pending):
                unresolved = sorted(deps.get(req_key, set()) - completed)
                deps_text = ",".join(f"{dep_idx}:{dep_track}" for dep_idx, dep_track in unresolved) or "none"
                details.append(f"{req_key[0]}:{req_key[1]}<-{deps_text}")
            sample = "; ".join(details[:8])
            run_errors.append(
                f"dependency_deadlock: {len(pending)} task(s) blocked with unresolved dependencies. "
                f"sample={sample}"
            )
            for section_idx, track_key in sorted(pending):
                run_errors.append(
                    f"section={section_idx} track={track_key}: skipped_due_to_dependency_deadlock"
                )
            break

        await asyncio.gather(*[_run_request(key) for key in ready])
        for req_key in ready:
            pending.discard(req_key)
            completed.add(req_key)

    tracks_named: Dict[str, List[GeneratedTrack]] = {}
    for section_idx in range(total_sections):
        section_name = section_name_by_idx[section_idx]
        tracks_named[section_name] = tracks_by_section_idx.get(section_idx, [])

    return {"tracks": tracks_named, "errors": run_errors}
