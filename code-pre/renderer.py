from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import random
import math

import mido

# 你自己的 schema
# from schema import ArrangementSchema, TrackSchema, TrackVariant, NoteSchema, GrooveIntent

# -----------------------------
# 基础参数与工具
# -----------------------------

PPQ_DEFAULT = 480  # ticks per beat

def bpm_to_tempo(bpm: int) -> int:
    # 将 BPM（每分钟节拍数）转换为 MIDI 标准的 “每拍微秒数”。
    return mido.bpm2tempo(bpm)

def ms_to_ticks(ms: float, bpm: int, ppq: int) -> int:
    # 将 “毫秒（ms）” 转换为 MIDI 的 “ticks”
    return int(round(ms * bpm * ppq / 60000.0))

def clamp_int(x: int, lo: int, hi: int) -> int:
    # 整数 “限幅”
    return max(lo, min(hi, x))

def bar_ticks(ppq: int, time_signature: Tuple[int, int] = (4, 4)) -> int:
    # 计算一个 “小节（bar）” 对应的 ticks 数
    beats_per_bar = time_signature[0]
    return beats_per_bar * ppq

def step16_ticks(ppq: int) -> int:
    # 计算一个 “16 分音符” 对应的 ticks 数
    return ppq // 4

# -----------------------------
# Groove preset（核心：Intent → 数值）
# -----------------------------

@dataclass(frozen=True)
class GrooveParams:
    swing_ratio: float            # 0.50=直，越大越 swing（推偶数16分）
    timing_jitter_ticks: int      # 抖动范围（±）
    velocity_jitter: int          # 力度抖动范围（±）
    push_ticks: int               # 整体推拉（+拖拍 / -抢拍）

def groove_preset(intent, bpm: int, ppq: int) -> GrooveParams:
    """
    intent: GrooveIntent (humanize_level, swing, feel)
    """
    # swing 档位 → ratio（0.50 直，0.58 常用 mid）
    swing_map = {
        "off": 0.50,
        "low": 0.54,
        "mid": 0.58,
        "high": 0.62,
    }

    # humanize 档位 → jitter ms / vel
    humanize_map = {
        "off":  (0.0, 0),
        "low":  (3.0, 5),
        "mid":  (8.0, 10),
        "high": (15.0, 18),
    }

    # feel 推拉 ms（整体更“靠前/靠后”）
    feel_map = {
        "tight": -6.0,
        "neutral": 0.0,
        "laid_back": 10.0,
    }

    swing_ratio = swing_map.get(getattr(intent, "swing", "mid"), 0.58)
    jitter_ms, vel_j = humanize_map.get(getattr(intent, "humanize_level", "mid"), (8.0, 10))
    push_ms = feel_map.get(getattr(intent, "feel", "neutral"), 0.0)

    return GrooveParams(
        swing_ratio=swing_ratio,
        timing_jitter_ticks=ms_to_ticks(jitter_ms, bpm, ppq),
        velocity_jitter=vel_j,
        push_ticks=ms_to_ticks(push_ms, bpm, ppq),
    )

# -----------------------------
# 轨道角色判定（用于不同强度处理）
# -----------------------------

@dataclass(frozen=True)
class RoleFactors:
    swing_mult: float
    jitter_mult: float
    vel_mult: float
    # 额外固定偏移（比如 clap 略拖拍）
    extra_push_ticks: int = 0

def infer_role(track_name: str, instrument: str) -> str:
    n = (track_name or "").lower()
    ins = (instrument or "").lower()

    # 简单启发式：你后续可以更精细（比如按 instrument 枚举）
    if "kick" in n or (ins == "drum" and "kick" in n):
        return "kick"
    if ins == "drum" and ("hat" in n or "hihat" in n or "hh" in n or "perc" in n):
        return "hat"
    if ins == "drum" and ("clap" in n or "snare" in n):
        return "clap"
    if "bass" in n or "bass" in ins:
        return "bass"
    if "chord" in n or "pad" in n or "chords" in ins or "pad" in ins:
        return "chords"
    if "melody" in n or "lead" in n or "lead" in ins:
        return "melody"
    if ins == "drum":
        return "drums_other"
    return "other"

def role_factors(role: str, bpm: int, ppq: int) -> RoleFactors:
    # 统一：hat 最吃 swing/jitter，kick 最稳，bass 很稳，melody/chords 适中
    if role == "kick":
        return RoleFactors(swing_mult=0.0, jitter_mult=0.15, vel_mult=0.2)
    if role == "clap":
        return RoleFactors(swing_mult=0.0, jitter_mult=0.6, vel_mult=0.6, extra_push_ticks=ms_to_ticks(6.0, bpm, ppq))
    if role == "hat":
        return RoleFactors(swing_mult=1.0, jitter_mult=1.0, vel_mult=1.0)
    if role == "bass":
        return RoleFactors(swing_mult=0.0, jitter_mult=0.35, vel_mult=0.35)
    if role == "chords":
        return RoleFactors(swing_mult=0.25, jitter_mult=0.4, vel_mult=0.25)
    if role == "melody":
        return RoleFactors(swing_mult=0.35, jitter_mult=0.5, vel_mult=0.4)
    if role == "drums_other":
        return RoleFactors(swing_mult=0.4, jitter_mult=0.7, vel_mult=0.7)
    return RoleFactors(swing_mult=0.2, jitter_mult=0.3, vel_mult=0.3)

# -----------------------------
# Swing / Humanize 应用（批量）
# -----------------------------

def apply_swing_to_tick(start_tick: int, bpm: int, ppq: int, swing_ratio: float, swing_mult: float) -> int:
    """
    只对“偶数 16分”做后移（offbeat 16th）。
    swing_ratio: 0.50=直；>0.50 推后
    """
    if swing_mult <= 0.0:
        return start_tick

    bar_t = bar_ticks(ppq)
    step_t = step16_ticks(ppq)
    pos = start_tick % bar_t
    step = pos // step_t  # 0..15

    # 偶数 16th（1,3,5..）推后
    if step % 2 == 1:
        # shift = (ratio-0.5) * step_ticks
        base_shift = (swing_ratio - 0.50) * step_t
        shift = int(round(base_shift * swing_mult))
        return start_tick + max(0, shift)
    return start_tick

def apply_humanize(
    start_tick: int,
    duration_tick: int,
    velocity: int,
    params: GrooveParams,
    factors: RoleFactors,
    rng: random.Random
) -> Tuple[int, int, int]:
    # push/pull（整体）
    t = start_tick + params.push_ticks + factors.extra_push_ticks

    # jitter（随机）
    jit = int(round(params.timing_jitter_ticks * factors.jitter_mult))
    if jit > 0:
        t += rng.randint(-jit, jit)

    # velocity jitter
    vj = int(round(params.velocity_jitter * factors.vel_mult))
    v = velocity
    if vj > 0:
        v += rng.randint(-vj, vj)
    v = clamp_int(v, 1, 127)

    # duration：保持简单（MVP 不做复杂抖动，避免鼓乱）
    d = max(1, duration_tick)
    return max(0, t), d, v

# -----------------------------
# 变体派生（最小可用版）
# -----------------------------

def derive_variant_tag_for_section(tag: str) -> str:
    # 允许 blueprint 里写错大小写
    return (tag or "core").lower()

def find_variant(track, tag: str):
    tag = derive_variant_tag_for_section(tag)
    for v in getattr(track, "variants", []):
        if getattr(v, "tag", "core") == tag:
            return v
    # fallback: core
    for v in getattr(track, "variants", []):
        if getattr(v, "tag", "core") == "core":
            return v
    return None

def apply_section_arrangement_and_presence(
    notes: List,
    section_tick_range: Tuple[int, int],
    variant_tag: str,
    presence: float,
    rng: random.Random
) -> List:
    """
    在一个段落范围内，对 notes 做 mute/light/break/build/full 的最小处理。
    注意：这里不重写整首，只在 [start,end) 范围内做过滤/缩放。
    """
    start_t, end_t = section_tick_range
    tag = derive_variant_tag_for_section(variant_tag)
    p = float(presence) if presence is not None else 1.0
    p = max(0.0, min(1.0, p))

    out = []
    for n in notes:
        st = getattr(n, "start_tick")
        if st < start_t or st >= end_t:
            out.append(n)
            continue

        # mute：直接去掉该段落内音符
        if tag == "mute":
            continue

        # light：随机删一部分（弱化密度） + 降低力度
        if tag == "light":
            # 删除概率：presence 越低删越多（0.0→70%删；1.0→25%删）
            drop_prob = 0.25 + (1.0 - p) * 0.45
            if rng.random() < drop_prob:
                continue
            # 轻量化力度
            new_v = int(round(getattr(n, "velocity") * (0.65 + 0.25 * p)))
            setattr(n, "velocity", clamp_int(new_v, 1, 127))
            out.append(n)
            continue

        # break：更强的减法（默认删更多）
        if tag == "break":
            drop_prob = 0.55 + (1.0 - p) * 0.25
            if rng.random() < drop_prob:
                continue
            new_v = int(round(getattr(n, "velocity") * (0.55 + 0.20 * p)))
            setattr(n, "velocity", clamp_int(new_v, 1, 127))
            out.append(n)
            continue

        # build/full：主要通过力度加强（密度增强可以后续加模板）
        if tag in ("build", "full"):
            boost = 0.90 + 0.60 * p  # 0.9~1.5
            new_v = int(round(getattr(n, "velocity") * boost))
            setattr(n, "velocity", clamp_int(new_v, 1, 127))
            out.append(n)
            continue

        # core/fill：暂时不额外处理（fill 建议未来单独插模板）
        out.append(n)

    return out

# -----------------------------
# MIDI 写入：notes → mido messages
# -----------------------------

def notes_to_mido_track(notes: List, channel: int, name: str) -> mido.MidiTrack:
    """
    输入 notes（start_tick绝对），输出一个带 delta time 的 MidiTrack
    """
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=name, time=0))

    events = []
    for n in notes:
        st = int(getattr(n, "start_tick"))
        dur = int(getattr(n, "duration_tick"))
        vel = int(getattr(n, "velocity"))
        pitch = int(getattr(n, "pitch"))

        events.append((st, True, pitch, vel))          # note_on
        events.append((st + max(1, dur), False, pitch, 0))  # note_off

    # sort: time, note_off before note_on at same time to avoid stuck notes
    events.sort(key=lambda x: (x[0], 0 if not x[1] else 1))

    last_time = 0
    for t, is_on, pitch, vel in events:
        delta = max(0, t - last_time)
        last_time = t
        if is_on:
            track.append(mido.Message("note_on", note=pitch, velocity=vel, channel=channel, time=delta))
        else:
            track.append(mido.Message("note_off", note=pitch, velocity=0, channel=channel, time=delta))

    return track

# -----------------------------
# 主入口：渲染整个 ArrangementSchema → .mid
# -----------------------------

def render_arrangement_to_midi(
    arrangement,  # ArrangementSchema
    out_path: str = "out.mid",
    ppq: int = PPQ_DEFAULT,
    seed: Optional[int] = None,
    time_signature: Tuple[int, int] = (4, 4),
) -> str:
    """
    默认输出：一个 multi-track MIDI 文件
    """
    blueprint = arrangement.blueprint
    bpm = int(getattr(blueprint, "bpm"))
    tempo = bpm_to_tempo(bpm)

    rng = random.Random(seed if seed is not None else hash(getattr(blueprint, "song_name", "song")) & 0xFFFFFFFF)

    mid = mido.MidiFile(ticks_per_beat=ppq)

    # Meta track
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    meta.append(mido.MetaMessage("time_signature", numerator=time_signature[0], denominator=time_signature[1], time=0))
    meta.append(mido.MetaMessage("track_name", name=getattr(blueprint, "song_name", "Song"), time=0))
    mid.tracks.append(meta)

    # 全局 groove
    global_intent = getattr(blueprint, "groove_global", None)
    global_params = groove_preset(global_intent, bpm=bpm, ppq=ppq) if global_intent else groove_preset(type("x",(object,),{"swing":"mid","humanize_level":"mid","feel":"neutral"})(), bpm, ppq)

    # 每条轨道渲染
    for track in arrangement.tracks:
        track_name = getattr(track, "name")
        instrument = getattr(track, "instrument")
        channel = int(getattr(track, "channel", 0))

        # track 级 groove（没有就用全局）
        intent = getattr(track, "groove_intent", None) or global_intent
        params = groove_preset(intent, bpm=bpm, ppq=ppq) if intent else global_params

        role = infer_role(track_name, instrument)
        factors = role_factors(role, bpm=bpm, ppq=ppq)

        # 选 core 作为基础
        core_variant = find_variant(track, "core")
        if core_variant is None:
            continue

        # NOTE: 为了在 section 内修改，我们复制一份 note 对象（避免改到原数据）
        base_notes = []
        for n in getattr(core_variant, "notes", []):
            # 用一个轻量对象承载字段（也可以改成 NoteSchema.model_copy）
            class _N: pass
            nn = _N()
            nn.pitch = n.pitch
            nn.start_tick = n.start_tick
            nn.duration_tick = n.duration_tick
            nn.velocity = n.velocity
            base_notes.append(nn)

        # 按 sections 的 arrangement/presence 修改（在区间内）
        bt = bar_ticks(ppq, time_signature)
        for sec in blueprint.sections:
            sec_name = getattr(sec, "name")
            start_bar = int(getattr(sec, "start_bar"))
            end_bar = int(getattr(sec, "end_bar"))

            sec_start_tick = (start_bar - 1) * bt
            sec_end_tick = end_bar * bt

            arrangement_map: Dict[str, str] = getattr(sec, "arrangement", {}) or {}
            presence_map: Dict[str, float] = getattr(sec, "presence", {}) or {}

            # blueprint 中用 track key 建议统一：比如 drums/bass/chords/melody
            # 这里我们用 instrument 或 track.name 来尝试匹配（你后续可规范化）
            key_candidates = [
                track_name.lower(),
                instrument.lower(),
            ]
            chosen_tag = None
            chosen_presence = None
            for k in key_candidates:
                if k in arrangement_map:
                    chosen_tag = arrangement_map[k]
                if k in presence_map:
                    chosen_presence = presence_map[k]

            # 没配就默认 core + presence=1
            tag = chosen_tag or "core"
            pres = chosen_presence if chosen_presence is not None else 1.0

            base_notes = apply_section_arrangement_and_presence(
                notes=base_notes,
                section_tick_range=(sec_start_tick, sec_end_tick),
                variant_tag=tag,
                presence=pres,
                rng=rng,
            )

        # 对所有 notes 批量应用 swing + humanize
        processed = []
        for n in base_notes:
            # swing（结构性）
            st = apply_swing_to_tick(
                start_tick=n.start_tick,
                bpm=bpm,
                ppq=ppq,
                swing_ratio=params.swing_ratio,
                swing_mult=factors.swing_mult,
            )

            # humanize（随机性 + 推拉）
            st2, dur2, vel2 = apply_humanize(
                start_tick=st,
                duration_tick=n.duration_tick,
                velocity=n.velocity,
                params=params,
                factors=factors,
                rng=rng,
            )

            n.start_tick = st2
            n.duration_tick = dur2
            n.velocity = vel2
            processed.append(n)

        # 写入 track
        mid.tracks.append(notes_to_mido_track(processed, channel=channel, name=track_name))

    mid.save(out_path)
    return out_path
