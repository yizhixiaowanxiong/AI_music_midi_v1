import asyncio
from collections import defaultdict
import inspect
import os
import time
from typing import Any, Dict, List, Set, Tuple

from schema.arrangement import (
    GeneratedTrack,
    PitchRange,
    RhythmConstraint,
    TrackContext,
    TrackGenRequest,
)
from schema.base import AgentRoutingRole
from schema.blueprint_schema import SongBlueprint
from utils.context_tools import (
    extract_harmony_summary,
    extract_kick_summary,
    extract_last_bar_midi,
    extract_melody_summary,
)
from utils.constants import (
    FREQ_BAND_HIGH,
    FREQ_BAND_LOW,
    FREQ_BAND_MID,
    FREQ_BAND_MIDI_RANGES,
)
from utils.context_summary import build_section_summary, compose_context_summary
from utils.channel_allocator import MidiChannelAllocator
from utils.dispatch import dispatch_section_to_requests
from utils.runner import clear_run_runtime, run_one_track

RequestKey = Tuple[int, str]  # (section_index, track_key)


def _env_float(name: str, default: float, min_value: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, value)


_SECTION_WALL_TIMEOUT_SEC = _env_float("SECTION_WALL_TIMEOUT_SEC", default=180.0, min_value=0.0)


def _env_int(name: str, default: int, min_value: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, value)


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw not in {"0", "false", "no", "off"}


_SECTION_ENERGY_STEP_MAX = _env_int("SECTION_ENERGY_STEP_MAX", default=1, min_value=0)
_SECTION_CONTINUITY_ALL_ROLES = _env_truthy("SECTION_CONTINUITY_ALL_ROLES", default=True)


def _section_key(section_idx: int, section: Any) -> str:
    return f"{section_idx}:{section.name}"


def _safe_energy_level(section: Any, default: int = 3) -> int:
    try:
        value = int(getattr(section, "energy_level", default) or default)
    except Exception:
        value = int(default)
    return max(1, min(5, value))


def _energy_level_to_value(level: int) -> float:
    normalized = max(1, min(5, int(level)))
    return float((normalized - 1) / 4.0)


def _compute_continuity_energy_levels(sections: List[Any]) -> Dict[int, int]:
    if not sections:
        return {}

    out: Dict[int, int] = {}
    prev = _safe_energy_level(sections[0], default=3)
    out[0] = prev

    for idx in range(1, len(sections)):
        level = _safe_energy_level(sections[idx], default=prev)
        if _SECTION_ENERGY_STEP_MAX > 0:
            delta = level - prev
            if delta > _SECTION_ENERGY_STEP_MAX:
                level = prev + _SECTION_ENERGY_STEP_MAX
            elif delta < -_SECTION_ENERGY_STEP_MAX:
                level = prev - _SECTION_ENERGY_STEP_MAX
        level = max(1, min(5, level))
        out[idx] = level
        prev = level

    return out


def _motif_variation_hint(next_section: Any) -> str:
    name = str(getattr(next_section, "name", "") or "").strip().lower()
    function = str(getattr(next_section, "section_function", "") or "").strip().lower()
    text = f"{name} {function}"
    if any(key in text for key in ("build", "build-up", "pre", "推进")):
        return "core motif with acceleration variation"
    if any(key in text for key in ("drop", "chorus", "hook", "climax", "爆发")):
        return "core motif with amplified variation"
    if any(key in text for key in ("bridge", "break", "turn", "转折")):
        return "core motif with transition variation"
    if any(key in text for key in ("outro", "ending", "收尾")):
        return "core motif with reduced variation"
    return "core motif with light variation"


def _seed_section_contexts_for_continuity(
    *,
    sections: List[Any],
    section_contexts: Dict[int, TrackContext],
    energy_levels: Dict[int, int],
) -> None:
    for idx in range(len(sections)):
        section_contexts.setdefault(idx, TrackContext())

    for idx in range(1, len(sections)):
        prev_section = sections[idx - 1]
        cur_section = sections[idx]
        prev_level = int(energy_levels.get(idx - 1, _safe_energy_level(prev_section)))
        cur_level = int(energy_levels.get(idx, _safe_energy_level(cur_section)))
        transition = str(getattr(prev_section, "transition_to_next", "") or "").strip()

        next_ctx = section_contexts.setdefault(idx, TrackContext())
        rules = [
            f"section energy handoff {prev_level}/5 -> {cur_level}/5, avoid abrupt jumps",
        ]
        if transition:
            rules.append(f"section entry aligns with previous transition: {transition}")
        next_ctx.locked_rhythm_rules = _merge_text_list(
            list(next_ctx.locked_rhythm_rules or []),
            rules,
        )
        if not str(next_ctx.core_motif or "").strip():
            next_ctx.core_motif = _motif_variation_hint(cur_section)


def _switch_section_context(
    *,
    section_idx: int,
    sections: List[Any],
    section_contexts: Dict[int, TrackContext],
    energy_levels: Dict[int, int],
) -> None:
    next_idx = section_idx + 1
    if next_idx >= len(sections):
        return

    cur_section = sections[section_idx]
    next_section = sections[next_idx]
    current_ctx = section_contexts.setdefault(section_idx, TrackContext())
    next_ctx = section_contexts.setdefault(next_idx, TrackContext())

    inherited_motif = str(current_ctx.core_motif or "").strip()
    variation_hint = _motif_variation_hint(next_section)
    if inherited_motif:
        next_ctx.core_motif = f"{inherited_motif}; {variation_hint}"
    elif not str(next_ctx.core_motif or "").strip():
        next_ctx.core_motif = variation_hint

    transition = str(getattr(cur_section, "transition_to_next", "") or "").strip()
    prev_level = int(energy_levels.get(section_idx, _safe_energy_level(cur_section)))
    next_level = int(energy_levels.get(next_idx, _safe_energy_level(next_section)))
    rules = [f"section energy handoff {prev_level}/5 -> {next_level}/5, keep continuity"]
    if transition:
        rules.append(f"section entry aligns with previous transition: {transition}")
    next_ctx.locked_rhythm_rules = _merge_text_list(list(next_ctx.locked_rhythm_rules or []), rules)


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

        needs_prev_section_context = role in (AgentRoutingRole.HARMONY, AgentRoutingRole.MELODY)
        if _SECTION_CONTINUITY_ALL_ROLES:
            needs_prev_section_context = True

        if section_idx > 0 and needs_prev_section_context:
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


def _merge_text_list(current: List[str], incoming: List[str]) -> List[str]:
    seen = {str(x).strip() for x in list(current or []) if str(x).strip()}
    out = [x for x in list(current or []) if str(x).strip()]
    for item in list(incoming or []):
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out[:6]


def _to_pitch_range(item: Any) -> PitchRange | None:
    if isinstance(item, PitchRange):
        return item
    if isinstance(item, dict):
        try:
            return PitchRange(**item)
        except Exception:
            return None
    low = getattr(item, "low_midi", None)
    high = getattr(item, "high_midi", None)
    if low is None or high is None:
        return None
    try:
        return PitchRange(low_midi=int(low), high_midi=int(high))
    except Exception:
        return None


def _merge_pitch_ranges(current: List[Any], incoming: List[Any]) -> List[PitchRange]:
    out: List[PitchRange] = []
    seen: Set[Tuple[int, int]] = set()
    for raw in list(current or []) + list(incoming or []):
        item = _to_pitch_range(raw)
        if item is None:
            continue
        key = (int(item.low_midi), int(item.high_midi))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:6]


def _band_pitch_range(band: str) -> PitchRange | None:
    low_high = FREQ_BAND_MIDI_RANGES.get(str(band))
    if not low_high:
        return None
    low, high = low_high
    return PitchRange(low_midi=int(low), high_midi=int(high))


def _to_rhythm_constraint(item: Any) -> RhythmConstraint | None:
    if isinstance(item, RhythmConstraint):
        return item
    if isinstance(item, dict):
        try:
            return RhythmConstraint(**item)
        except Exception:
            return None
    kind = getattr(item, "kind", None)
    if kind is None:
        return None
    anchor_ticks = getattr(item, "anchor_ticks", None) or []
    payload = getattr(item, "payload", None) or {}
    try:
        return RhythmConstraint(
            kind=str(kind),
            anchor_ticks=[int(x) for x in list(anchor_ticks or [])],
            payload=dict(payload or {}),
        )
    except Exception:
        return None


def _constraint_key(item: RhythmConstraint) -> Tuple[str, Tuple[int, ...], Tuple[Tuple[str, str], ...]]:
    payload_items = tuple(sorted((str(k), str(v)) for k, v in dict(item.payload or {}).items()))
    return (str(item.kind), tuple(int(x) for x in list(item.anchor_ticks or [])), payload_items)


def _merge_rhythm_constraints(current: List[Any], incoming: List[Any]) -> List[RhythmConstraint]:
    out: List[RhythmConstraint] = []
    seen: Set[Tuple[str, Tuple[int, ...], Tuple[Tuple[str, str], ...]]] = set()
    for raw in list(current or []) + list(incoming or []):
        item = _to_rhythm_constraint(raw)
        if item is None:
            continue
        key = _constraint_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:8]


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _context_budget_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {
            "measured_tracks": 0,
            "over_budget_tracks": 0,
            "token_budget": _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", default=300, min_value=1),
            "avg_total_tokens": 0.0,
            "p95_total_tokens": 0,
            "max_total_tokens": 0,
            "top_over_budget": [],
        }

    totals = [_safe_int(row.get("context_total_tokens"), 0) for row in rows]
    sorted_totals = sorted(totals)
    p95_idx = int(round((len(sorted_totals) - 1) * 0.95))
    p95 = sorted_totals[p95_idx]
    over_rows = [row for row in rows if _safe_int(row.get("over_budget"), 0) > 0]
    budget_values = [_safe_int(row.get("token_budget"), 0) for row in rows if _safe_int(row.get("token_budget"), 0) > 0]
    token_budget = budget_values[0] if budget_values else _env_int("CONTEXT_SUMMARY_TOKEN_BUDGET", default=300, min_value=1)

    top_over_budget = sorted(
        over_rows,
        key=lambda row: _safe_int(row.get("context_total_tokens"), 0),
        reverse=True,
    )[:5]

    top_payload: List[Dict[str, Any]] = []
    for row in top_over_budget:
        top_payload.append(
            {
                "section_index": _safe_int(row.get("section_index"), 0),
                "section_name": str(row.get("section_name") or ""),
                "track_key": str(row.get("track_key") or ""),
                "instrument": str(row.get("instrument") or ""),
                "context_total_tokens": _safe_int(row.get("context_total_tokens"), 0),
                "token_budget": _safe_int(row.get("token_budget"), token_budget),
            }
        )

    return {
        "measured_tracks": len(rows),
        "over_budget_tracks": len(over_rows),
        "token_budget": token_budget,
        "avg_total_tokens": round(sum(totals) / max(1, len(totals)), 2),
        "p95_total_tokens": p95,
        "max_total_tokens": max(totals) if totals else 0,
        "top_over_budget": top_payload,
    }


_ROUTE_ROLE_ORDER = {
    AgentRoutingRole.PERCUSSION.value: 0,
    AgentRoutingRole.BASS.value: 1,
    AgentRoutingRole.HARMONY.value: 2,
    AgentRoutingRole.MELODY.value: 3,
    AgentRoutingRole.FX.value: 4,
}


def _role_text(value: Any) -> str:
    if isinstance(value, AgentRoutingRole):
        return str(value.value)
    return str(value or "").strip().lower()


def _route_priority(item: Dict[str, Any]) -> tuple[int, int, str]:
    return (
        _safe_int(item.get("compute_layer"), 0),
        int(_ROUTE_ROLE_ORDER.get(str(item.get("role") or ""), 9)),
        str(item.get("track_key") or ""),
    )


def _pick_first_by_role_text(
    candidates: List[Dict[str, Any]],
    role_text: str,
) -> List[Dict[str, Any]]:
    for candidate in candidates:
        if str(candidate.get("role") or "") == role_text:
            return [candidate]
    return []


def _apply_routed_track_cap(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cap = _env_int("SECTION_TRACK_SOFT_CAP", default=6, min_value=0)
    if cap <= 0 or len(candidates) <= cap:
        return sorted(list(candidates), key=_route_priority)

    ordered = sorted(candidates, key=_route_priority)
    picked: List[Dict[str, Any]] = []
    picked.extend(_pick_first_by_role_text(ordered, AgentRoutingRole.PERCUSSION.value))
    picked.extend(_pick_first_by_role_text(ordered, AgentRoutingRole.BASS.value))

    seen = {(str(item.get("track_key") or ""), str(item.get("role") or "")) for item in picked}
    for candidate in ordered:
        if len(picked) >= cap:
            break
        key = (str(candidate.get("track_key") or ""), str(candidate.get("role") or ""))
        if key in seen:
            continue
        picked.append(candidate)
        seen.add(key)

    return sorted(picked, key=_route_priority)


def _route_requests_with_cap(
    requests: List[TrackGenRequest],
) -> List[TrackGenRequest]:
    if not requests:
        return []

    candidates = [
        {
            "track_key": str(getattr(req, "track_key", "") or ""),
            "role": _role_text(getattr(req, "instrument", None)),
            "compute_layer": _safe_int(getattr(req, "compute_layer", 0), 0),
        }
        for req in requests
    ]
    accepted = _apply_routed_track_cap(candidates)
    if not accepted:
        return []

    order: Dict[str, int] = {}
    for idx, item in enumerate(accepted):
        key = str(item.get("track_key") or "")
        if key and key not in order:
            order[key] = idx

    selected = [req for req in requests if str(getattr(req, "track_key", "") or "") in order]
    selected.sort(
        key=lambda req: (
            int(order.get(str(getattr(req, "track_key", "") or ""), 10_000)),
            _safe_int(getattr(req, "compute_layer", 0), 0),
            str(getattr(req, "track_key", "") or ""),
        )
    )
    return selected


def _filter_supported_kwargs(fn: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signature = inspect.signature(fn)
    except Exception:
        return {}
    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return dict(kwargs)
    out: Dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in params:
            out[key] = value
    return out


async def _run_one_track_with_compat(
    req: TrackGenRequest,
    runtime_context: TrackContext,
    *,
    run_scope_id: str = "",
    session_scope_id: str = "",
) -> GeneratedTrack:
    kwargs = _filter_supported_kwargs(
        run_one_track,
        {
            "run_scope_id": run_scope_id,
            "session_scope_id": session_scope_id,
        },
    )
    return await run_one_track(req, runtime_context, **kwargs)


def _update_runtime_context(ctx: TrackContext, track: GeneratedTrack) -> None:
    role = getattr(track, "instrument", None)
    if role == AgentRoutingRole.PERCUSSION:
        ctx.occupied_frequency_bands = _merge_text_list(
            list(ctx.occupied_frequency_bands or []),
            [FREQ_BAND_LOW],
        )
        low_range = _band_pitch_range(FREQ_BAND_LOW)
        if low_range is not None:
            ctx.occupied_pitch_ranges = _merge_pitch_ranges(
                list(ctx.occupied_pitch_ranges or []),
                [low_range],
            )
        summary = extract_kick_summary(track)
        if summary:
            kick_onsets = list(summary.get("kick_onsets_ticks", []) or [])
            ctx.kick_onsets_ticks = _merge_unique_ints(
                list(ctx.kick_onsets_ticks or []),
                kick_onsets,
            )
            ctx.locked_rhythm_rules = _merge_text_list(
                list(ctx.locked_rhythm_rules or []),
                list(summary.get("locked_rhythm_rules") or []),
            )
            ctx.locked_rhythm_rules = _merge_text_list(
                list(ctx.locked_rhythm_rules or []),
                ["底鼓重音优先对齐，避免同tick冲突"],
            )
            new_constraints: List[RhythmConstraint] = []
            if kick_onsets:
                new_constraints.append(
                    RhythmConstraint(
                        kind="align_to_kick",
                        anchor_ticks=[int(x) for x in kick_onsets[:64]],
                    )
                )
            new_constraints.append(RhythmConstraint(kind="avoid_same_tick_overlap"))
            ctx.rhythm_constraints = _merge_rhythm_constraints(
                list(ctx.rhythm_constraints or []),
                new_constraints,
            )
    elif role == AgentRoutingRole.HARMONY:
        ctx.occupied_frequency_bands = _merge_text_list(
            list(ctx.occupied_frequency_bands or []),
            [FREQ_BAND_MID],
        )
        mid_range = _band_pitch_range(FREQ_BAND_MID)
        if mid_range is not None:
            ctx.occupied_pitch_ranges = _merge_pitch_ranges(
                list(ctx.occupied_pitch_ranges or []),
                [mid_range],
            )
        summary = extract_harmony_summary(track)
        if summary:
            ctx.chord_notes_per_bar = _merge_chord_notes_per_bar(
                list(ctx.chord_notes_per_bar or []),
                list(summary.get("chord_notes_per_bar", []) or []),
            )
    elif role == AgentRoutingRole.MELODY:
        ctx.occupied_frequency_bands = _merge_text_list(
            list(ctx.occupied_frequency_bands or []),
            [FREQ_BAND_HIGH],
        )
        high_range = _band_pitch_range(FREQ_BAND_HIGH)
        if high_range is not None:
            ctx.occupied_pitch_ranges = _merge_pitch_ranges(
                list(ctx.occupied_pitch_ranges or []),
                [high_range],
            )
        summary = extract_melody_summary(track)
        if summary:
            ctx.lyric_rhythm_ticks = _merge_unique_ints(
                list(ctx.lyric_rhythm_ticks or []),
                list(summary.get("lyric_rhythm_ticks", []) or []),
            )
            if not str(ctx.core_motif or "").strip():
                ctx.core_motif = "主旋律动机延续"


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
    blueprint = state.get("blueprint")
    if blueprint is None:
        raise ValueError("missing_blueprint: state.blueprint is required for section generation.")
    if not isinstance(blueprint, SongBlueprint):
        raise TypeError("invalid_blueprint: state.blueprint must be SongBlueprint.")

    strictness = int(state.get("strictness", 1) or 1)
    global_anchor_summary = str(state.get("global_anchor_summary") or "").strip()
    run_scope_id = str(state.get("run_id") or "").strip()
    session_scope_id = str(state.get("session_id") or "").strip()
    sections = list(blueprint.sections)
    total_sections = len(sections)
    continuity_energy_levels = _compute_continuity_energy_levels(sections)

    tracks_by_section_idx: Dict[int, List[GeneratedTrack]] = defaultdict(list)
    section_contexts: Dict[int, TrackContext] = {idx: TrackContext() for idx in range(total_sections)}
    _seed_section_contexts_for_continuity(
        sections=sections,
        section_contexts=section_contexts,
        energy_levels=continuity_energy_levels,
    )
    run_errors: List[str] = []
    context_budget_rows: List[Dict[str, Any]] = []
    channel_allocator = MidiChannelAllocator()

    req_map: Dict[RequestKey, TrackGenRequest] = {}
    role_keys_by_section: Dict[int, Dict[AgentRoutingRole, List[RequestKey]]] = defaultdict(
        lambda: defaultdict(list)
    )
    section_req_keys: Dict[int, Set[RequestKey]] = defaultdict(set)
    section_name_by_idx: Dict[int, str] = {}

    for section_idx, section in enumerate(sections):
        section_name_by_idx[section_idx] = _section_key(section_idx, section)
        # Keep dispatch as request builder only; routing/cap is applied by local scheduler logic below.
        requests = dispatch_section_to_requests(
            section=section,
            blueprint=blueprint,
            section_runtime_index=section_idx,
            strictness=strictness,
        )
        requests = _route_requests_with_cap(requests)
        for req in requests:
            effective_energy_level = int(
                continuity_energy_levels.get(section_idx, _safe_energy_level(section, default=3))
            )
            req.energy_level = _energy_level_to_value(effective_energy_level)
            req.section_summary = build_section_summary(
                section_name=str(getattr(req, "section_name", "") or ""),
                start_bar=int(getattr(req, "start_bar", 1) or 1),
                end_bar=int(getattr(req, "end_bar", 1) or 1),
                section_energy=float(req.energy_level),
                arrangement_size=len(getattr(section, "arrangement", {}) or {}),
                transition_to_next=str(getattr(section, "transition_to_next", "") or ""),
                section_energy_level=effective_energy_level,
                section_function=str(getattr(section, "section_function", "") or "").strip() or None,
            )
            if global_anchor_summary:
                if not str(getattr(req, "global_anchor_summary", "") or "").strip():
                    req.global_anchor_summary = global_anchor_summary
            req.context_summary = compose_context_summary(
                global_anchor_summary=str(getattr(req, "global_anchor_summary", "") or ""),
                section_summary=str(getattr(req, "section_summary", "") or ""),
            )
            req.midi_channel = channel_allocator.assign(req.track_key, req.instrument)
            key: RequestKey = (section_idx, req.track_key)
            req_map[key] = req
            section_req_keys[section_idx].add(key)
            role_keys_by_section[section_idx][req.instrument].append(key)

    if not req_map:
        return {"tracks": {}, "errors": []}

    deps = _build_dependency_graph(sections, role_keys_by_section, req_map)
    pending: Set[RequestKey] = set(req_map.keys())
    completed: Set[RequestKey] = set()
    switched_sections: Set[int] = set()
    started_at = time.perf_counter()
    while pending:
        if _SECTION_WALL_TIMEOUT_SEC > 0:
            elapsed = time.perf_counter() - started_at
            if elapsed >= _SECTION_WALL_TIMEOUT_SEC:
                run_errors.append(
                    f"section_generation_timeout: elapsed={elapsed:.2f}s cap={_SECTION_WALL_TIMEOUT_SEC:.2f}s"
                )
                for section_idx, track_key in sorted(pending):
                    run_errors.append(
                        f"section={section_idx} track={track_key}: skipped_due_to_section_timeout"
                    )
                break

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

        ready_exec: List[RequestKey] = []
        ready_tasks: List[asyncio.Task[GeneratedTrack]] = []
        for req_key in ready:
            section_idx, track_key = req_key
            req = req_map.get(req_key)
            if req is None:
                run_errors.append(
                    f"section={section_idx} track={track_key}: missing_request_in_batch_result"
                )
                continue
            ctx = section_contexts.setdefault(section_idx, TrackContext())
            ready_exec.append(req_key)
            ready_tasks.append(
                asyncio.create_task(
                    _run_one_track_with_compat(
                        req,
                        ctx,
                        run_scope_id=run_scope_id,
                        session_scope_id=session_scope_id,
                    )
                )
            )

        batch_results: List[Any] = []
        if ready_tasks:
            batch_results = await asyncio.gather(*ready_tasks, return_exceptions=True)

        for req_key, batch_result in zip(ready_exec, batch_results):
            section_idx, track_key = req_key
            req = req_map.get(req_key)

            if isinstance(batch_result, Exception):
                run_errors.append(f"section={section_idx} track={track_key}: {batch_result}")
                continue

            track = batch_result
            if not isinstance(track, GeneratedTrack):
                run_errors.append(f"section={section_idx} track={track_key}: invalid_batch_result_track")
                continue

            tracks_by_section_idx[section_idx].append(track)

            metrics = getattr(track, "metrics", None)
            if isinstance(metrics, dict):
                context_budget = metrics.get("context_budget")
                if isinstance(context_budget, dict):
                    row = dict(context_budget)
                    row["section_index"] = int(section_idx)
                    row["section_name"] = str(getattr(req, "section_name", "") or "")
                    row["track_key"] = str(getattr(req, "track_key", "") or "")
                    instrument = getattr(req, "instrument", None)
                    row["instrument"] = str(getattr(instrument, "value", instrument) or "")
                    context_budget_rows.append(row)

            if getattr(track, "error", None):
                run_errors.append(
                    f"section={section_idx} track={getattr(req, 'track_key', 'unknown')}: "
                    f"{str(track.error)}"
                )

            ctx = section_contexts.setdefault(section_idx, TrackContext())
            _update_runtime_context(ctx, track)
            _update_transition_context(
                section_idx=section_idx,
                total_sections=total_sections,
                track=track,
                section_contexts=section_contexts,
                role_keys_by_section=role_keys_by_section,
            )

        for req_key in ready:
            pending.discard(req_key)
            completed.add(req_key)

        for section_idx, keys in section_req_keys.items():
            if section_idx in switched_sections:
                continue
            if not keys or not keys.issubset(completed):
                continue
            _switch_section_context(
                section_idx=section_idx,
                sections=sections,
                section_contexts=section_contexts,
                energy_levels=continuity_energy_levels,
            )
            switched_sections.add(section_idx)

    tracks_named: Dict[str, List[GeneratedTrack]] = {}
    for section_idx in range(total_sections):
        section_name = section_name_by_idx[section_idx]
        tracks_named[section_name] = tracks_by_section_idx.get(section_idx, [])

    if run_scope_id:
        clear_run_runtime(run_scope_id)

    return {
        "tracks": tracks_named,
        "errors": run_errors,
        "context_budget": _context_budget_summary(context_budget_rows),
    }

