from typing import List, Dict, Any, Set, Tuple, Optional
from schema.drum_schema import DrumsSectionOutput, DrumNote


# ==========================================
# 公共工具：拍号/时值推导（全流程统一复用）
# ==========================================

def _parse_time_signature(ts: str) -> Tuple[int, int]:
    try:
        n, d = ts.split("/")
        return int(n), int(d)
    except Exception:
        return 4, 4


def compute_bar_ticks(out: DrumsSectionOutput) -> int:
    """
    统一计算 bar_ticks，避免不同模块各算各的导致不一致。
    公式: bar_ticks = TPB * numer * (4/denom)
    TPB 默认是四分音符 tick（ticks_per_beat）
    """
    tpb = int(getattr(out, "ticks_per_beat", 480) or 480)

    bt = int(getattr(out, "bar_ticks", 0) or 0)
    if bt > 0:
        return bt

    ts = getattr(out, "time_signature", "4/4") or "4/4"
    numer, denom = _parse_time_signature(ts)
    try:
        return int(tpb * numer * (4 / denom))
    except Exception:
        return tpb * 4


def _safe_note_copy(note: DrumNote) -> DrumNote:
    # Pydantic v2: model_copy; v1: copy
    if hasattr(note, "model_copy"):
        return note.model_copy()
    return note.copy()


def _get_pattern_len_bars(out: DrumsSectionOutput, pattern) -> int:
    """
    兼容 pattern_len_bars 既可能在 section_out，也可能未来下放到 pattern。
    """
    for attr in ("pattern_len_bars", "len_bars"):
        v = getattr(pattern, attr, None)
        if isinstance(v, int) and v > 0:
            return v

    v = getattr(out, "pattern_len_bars", None)
    if isinstance(v, int) and v > 0:
        return v

    return 4


# ==========================================
# 核心：Pattern+Phrase -> 线性绝对时间音符
# ==========================================

def flatten_drum_section(out: DrumsSectionOutput) -> List[DrumNote]:
    """
    将结构化的 Pattern+Phrase 展开为线性音符（相对 section_start_bar）。
    关键策略：phrase 必须完全落在 section 内，否则跳过（避免相位错）。
    """
    flat: List[DrumNote] = []

    tpb = int(getattr(out, "ticks_per_beat", 480) or 480)
    bar_ticks = compute_bar_ticks(out) or (tpb * 4)

    sec_start = int(out.section_start_bar)
    sec_end = int(out.section_end_bar)

    pattern_map = {p.tag: p for p in out.patterns}

    for ph in out.phrases:
        pat = pattern_map.get(ph.use_pattern_tag)
        if not pat:
            continue

        # phrase 必须完全落在 section 内，否则跳过（最稳，避免节奏相位错）
        if ph.start_bar < sec_start or ph.end_bar > sec_end:
            continue

        phrase_len = int(ph.end_bar - ph.start_bar + 1)
        if phrase_len <= 0:
            continue

        pat_len = _get_pattern_len_bars(out, pat)
        if pat_len <= 0:
            pat_len = 4

        phrase_abs_start_tick = (int(ph.start_bar) - sec_start) * bar_ticks
        phrase_abs_end_tick = phrase_abs_start_tick + phrase_len * bar_ticks  # exclusive

        cur_bar = 0
        while cur_bar < phrase_len:
            loop_tick = phrase_abs_start_tick + cur_bar * bar_ticks
            for note in pat.notes:
                new_note = _safe_note_copy(note)
                abs_tick = loop_tick + int(note.start_tick)
                if abs_tick >= phrase_abs_end_tick:
                    continue
                new_note.start_tick = abs_tick
                flat.append(new_note)
            cur_bar += pat_len

    flat.sort(key=lambda n: n.start_tick)
    return flat


# ==========================================
# 网格相关：1/16 量化 + 文本渲染
# ==========================================

def _kick_steps(kick_ticks: List[int], total_bars: int, bar_ticks: int, tpb: int) -> Set[Tuple[int, int]]:
    """
    返回 {(bar_idx, step_idx)}，bar_idx 为 section 内 0-based。
    """
    step_tick = max(1, int(tpb // 4))  # 1/16-note relative to quarter
    steps_per_bar = max(1, int(bar_ticks // step_tick))

    s: Set[Tuple[int, int]] = set()
    for t in kick_ticks:
        if t < 0:
            continue
        b = int(t // bar_ticks)
        if 0 <= b < total_bars:
            step = int((t % bar_ticks) // step_tick)
            if 0 <= step < steps_per_bar:
                s.add((b, step))
    return s


def _render_bar_16(kick_step_set: Set[Tuple[int, int]], bar_idx: int, bar_ticks: int, tpb: int) -> str:
    step_tick = max(1, int(tpb // 4))
    steps_per_bar = max(1, int(bar_ticks // step_tick))
    return "".join("K" if (bar_idx, i) in kick_step_set else "." for i in range(steps_per_bar))


def _render_full_grid(
    kick_step_set: Set[Tuple[int, int]],
    total_bars: int,
    bar_ticks: int,
    tpb: int,
    start_bar_abs: int,
    time_signature: str,
) -> str:
    """
    full 模式：按拍号分隔空格 + 连续重复 bars 压缩为范围
    """
    _, denom = _parse_time_signature(time_signature or "4/4")

    step_tick = max(1, int(tpb // 4))
    steps_per_bar = max(1, int(bar_ticks // step_tick))

    beat_ticks = max(1, int(tpb * (4 / denom)))
    steps_per_beat = max(1, int(beat_ticks // step_tick))

    def render_bar(b: int) -> str:
        s = []
        for step in range(steps_per_bar):
            s.append("K" if (b, step) in kick_step_set else ".")
            if (step + 1) % steps_per_beat == 0:
                s.append(" ")
        return "".join(s).strip()

    lines: List[str] = []
    last = None
    run_start = 0

    def flush_run(bar_str: str, s_idx: int, e_idx: int):
        if s_idx == e_idx:
            sec_b = s_idx + 1
            abs_b = start_bar_abs + s_idx
            lines.append(f"Bar {sec_b} (Abs {abs_b}): | {bar_str} |")
        else:
            sec_s, sec_e = s_idx + 1, e_idx + 1
            abs_s, abs_e = start_bar_abs + s_idx, start_bar_abs + e_idx
            lines.append(f"Bars {sec_s}-{sec_e} (Abs {abs_s}-{abs_e}): | {bar_str} | (Repeat)")

    for b in range(total_bars):
        cur = render_bar(b)
        if last is None:
            last = cur
            run_start = b
            continue
        if cur == last:
            continue
        flush_run(last, run_start, b - 1)
        last = cur
        run_start = b

    if last is not None:
        flush_run(last, run_start, total_bars - 1)

    return "\n".join(lines)


def _break_bars(kick_ticks: List[int], total_bars: int, bar_ticks: int) -> List[int]:
    """
    返回 section 内 1-based 的 break bars（整小节没有 kick）。
    """
    has = [False] * total_bars
    for t in kick_ticks:
        if t < 0:
            continue
        b = int(t // bar_ticks)
        if 0 <= b < total_bars:
            has[b] = True
    return [i + 1 for i, v in enumerate(has) if not v]


def _density_desc(kick_ticks: List[int], total_bars: int) -> str:
    avg = len(kick_ticks) / max(1, total_bars)
    if avg >= 3.5:
        return "Driving (4-on-floor)"
    if avg >= 2:
        return "Standard Groove"
    return "Sparse"


# ==========================================
# 统一入口：mode = "min" | "full"
# ==========================================

def summarize_drums_for_bass(out: DrumsSectionOutput, mode: str = "min") -> Dict[str, Any]:
    """
    mode:
      - "min": LLM 语义优先，输出更短（推荐默认）
      - "full": 工程稳健，输出全段落网格（token 更长）
    """
    mode = (mode or "min").lower().strip()
    if mode not in ("min", "full"):
        mode = "min"

    flat = flatten_drum_section(out)
    kick_ticks = [int(n.start_tick) for n in flat if int(n.pitch) == 36]

    tpb = int(getattr(out, "ticks_per_beat", 480) or 480)
    ts = getattr(out, "time_signature", "4/4") or "4/4"
    bar_ticks = compute_bar_ticks(out) or (tpb * 4)

    sec_start = int(out.section_start_bar)
    sec_end = int(out.section_end_bar)
    total_bars = max(0, sec_end - sec_start + 1)

    breaks = _break_bars(kick_ticks, total_bars, bar_ticks)
    density = _density_desc(kick_ticks, total_bars)

    # 统一给到代码层的“可复用结构”：
    # 即使你当前不写硬规则，也方便未来扩展
    kick_step_set = _kick_steps(kick_ticks, total_bars, bar_ticks, tpb)

    # ========== LLM 文本 ==========
    if mode == "full":
        grid = _render_full_grid(
            kick_step_set,
            total_bars,
            bar_ticks,
            tpb,
            start_bar_abs=sec_start,
            time_signature=ts,
        )
        breaks_text = f"Kick Breaks (no kick): {breaks if breaks else 'None'}"
        llm_text = (
            f"Kick Drum Pattern [{out.section_name}] ({density}) [time_signature={ts}]:\n"
            f"{grid}\n"
            f"{breaks_text}\n"
            "Legend: K=kick, .=silence. Grid is 1/16-note; spacing follows beats."
        )
    else:
        # min：只给最多 4 个“代表性 bar”
        picks: List[int] = []
        if total_bars >= 1:
            picks.append(1)
        if breaks:
            picks.append(breaks[0])
        if total_bars >= 2:
            picks.append(total_bars)
        # 去重 + 限制数量
        picks = list(dict.fromkeys([b for b in picks if 1 <= b <= total_bars]))[:4]

        examples = []
        for b in picks:
            bar_str = _render_bar_16(kick_step_set, b - 1, bar_ticks, tpb)
            examples.append(f"Bar {b} (Abs {sec_start + b - 1}): {bar_str}")

        llm_text = (
            f"Kick Summary [{out.section_name}] ({density}) [time_signature={ts}]:\n"
            f"Break bars (no kick): {breaks if breaks else 'None'}\n"
            f"Examples (1/16):\n" + "\n".join(examples) +
            "\nLegend: K=kick, .=silence"
        )

    return {
        "section_name": out.section_name,
        "llm_context_text": llm_text,

        # 数据层（保留精确与可用结构）
        "kick_onsets_ticks": kick_ticks,
        "bar_ticks": bar_ticks,
        "total_bars": total_bars,
        "break_bars": breaks,

        # 未来写强规则时很方便（可忽略不用）
        "kick_steps": sorted(list(kick_step_set)),  # list[(bar_idx, step_idx)]
        "mode": mode,
    }
