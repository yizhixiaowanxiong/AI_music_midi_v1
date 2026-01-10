from typing import List
import mido
from mido import MidiFile,MidiTrack,Message,MetaMessage
import random
from schema import ArrangementSchema, TrackSchema

# === 乐器映射协议 ===
# Key: schema 里的乐器名 -> Value: MIDI Channel (0-15)
# 对应 FL Studio: Channel 0 -> Port 1, Channel 1 -> Port 2...
INSTRUMENT_MAPPING = {
    "piano":      {"channel": 0,  "program": 0},   # FL Ch 1 基础旋律乐器
    "bass":       {"channel": 1,  "program": 33},  # FL Ch 2 低频节奏乐器
    "synth_lead": {"channel": 2,  "program": 81},  # FL Ch 3 主旋律/高光音色
    "synth_pad":  {"channel": 3,  "program": 89},  # FL Ch 4 氛围/和声
    "strings":    {"channel": 4,  "program": 48},  # FL Ch 5 弦乐/古典抒情
    "fx":         {"channel": 5,  "program": 96},  # FL Ch 6 特殊音效
    "drum":       {"channel": 9,  "program": 0}    # FL Ch 10 (MIDI 标准打击乐)
}

CC_VOLUME = 7
CC_PAN = 10
CC_CUTOFF = 74

# @dataclass
# # 音符类 描述单个 MIDI 音符
# class Note:
#     pitch : int #midi音高 0-127 C4=60
#     duration : int #持续时间 以tick为单位
#     velocity : int #力度0-127
#     start_time : int #开始时间 绝对时间tick
# @dataclass
# # 音轨类 管理多个音符的集合
# class Track:
#     name : str #音轨名称，用于标识音轨用途
#     notes : List[Note] #音符列表 管理音轨下的Note实例

# midi文件生成
def generate_midi_file(schema_data: ArrangementSchema,filename:str="output.midi"):
    """
        Service 层入口：接收 Agent 的数据对象，生成 MIDI 文件
    """
    mid = MidiFile(type=1)
    mid.ticks_per_beat = 480
    tempo = mido.bpm2tempo(schema_data.bpm)
    # 1.全局控制轨
    global_track=MidiTrack()
    mid.tracks.append(global_track)
    global_track.append(MetaMessage('track_name',name="Global Control",time=0))
    global_track.append(MetaMessage('set_tempo', tempo=tempo, time=0))
    # 写入标记结构(Markers)
    sorted_markers=sorted(schema_data.sections,key=lambda x: x.start_time)
    last_marker_time=0
    for marker in sorted_markers:
        delta = marker.start_time - last_marker_time
        if delta < 0: delta = 0
        global_track.append(MetaMessage('marker', text=marker.name, time=delta))
        last_marker_time = marker.start_time
    # 2. 乐器轨
    for schema_track in schema_data.tracks:
        midi_track = MidiTrack()
        mid.tracks.append(midi_track)

        # 获取通道映射
        mapping = INSTRUMENT_MAPPING.get(schema_track.instrument, {"channel": 0, "program": 0})
        channel = mapping["channel"]

        midi_track.append(MetaMessage('track_name', name=schema_track.name, time=0))
        midi_track.append(Message('program_change', channel=channel, program=mapping["program"], time=0))

        # 循环参数获取
        loop_count = getattr(schema_track, 'loop_count', 1)
        pattern_len = getattr(schema_track, 'pattern_length', 0)
        # 智能容错：如果 AI 忘了给 pattern_length，自动计算最后一个音符的结束时间作为长度
        if loop_count > 1 and pattern_len == 0 and schema_track.notes:
            # 找出所有音符中最后结束的时间点
            pattern_len = max([n.start_time + n.duration for n in schema_track.notes])

        # === Day 1 新增：人性化处理 ===
        # 在生成 MIDI 事件之前，给音符注入"人性"
        if schema_track.notes:  # 确保有音符才处理
            humanize_event(schema_track, schema_track.instrument)
            print(f"   🎨 已对 {schema_track.name} 进行人性化处理")

        # 音符事件
        events = []
        for i in range(loop_count):
            # 计算当前这一遍循环的【时间偏移量】
            # 第1遍偏移 0，第2遍偏移 pattern_len，第3遍偏移 2*pattern_len...
            time_offset = i * pattern_len

            # 处理音符
            for note in schema_track.notes:
                # 关键：实际时间 = 音符原始开始时间 + 当前循环偏移量
                abs_start = note.start_time + time_offset
                abs_end = abs_start + note.duration

                events.append({
                    "type": "note_on",
                    "pitch": note.pitch,
                    "velocity": note.velocity,
                    "time": abs_start,  # 使用计算后的绝对时间
                    "channel": channel
                })
                events.append({
                    "type": "note_off",
                    "pitch": note.pitch,
                    "velocity": 0,
                    "time": abs_end,  # 使用计算后的绝对时间
                    "channel": channel
                })
        # 自动化控制
        if schema_track.automations:
            for auto in schema_track.automations:
                cc_num=CC_VOLUME
                if auto.type == "pan": cc_num=CC_PAN
                elif auto.type == "cutoff": cc_num = CC_CUTOFF
                steps = 15
                val_range = auto.end_val - auto.start_val
                step_time = auto.duration / steps
                for i in range(steps + 1):
                    current_val = int(auto.start_val + (val_range * (i / steps)))
                    current_time = int(auto.start_time + (step_time * i))
                    events.append(
                        {"type": "control_change", "control": cc_num, "value": current_val, "time": current_time,
                         "channel": channel})
        # 排序与写入
        events.sort(key=lambda x: x["time"])
        last_time = 0
        for event in events:
            delta = event["time"] - last_time
            if delta < 0: delta = 0

            if event["type"] == "control_change":
                midi_track.append(Message('control_change', channel=event["channel"], control=event["control"],
                                          value=event["value"], time=delta))
            else:
                midi_track.append(Message(event["type"], note=event["pitch"], velocity=event["velocity"],
                                          channel=event["channel"], time=delta))
            last_time = event["time"]

        print(f"   🎹 Track: {schema_track.name} -> CH {channel + 1}")

    mid.save(filename)
    print(f"✅ 完成: {filename}")

# === 人性化处理 ===
def humanize_event(track_data: TrackSchema, instrument_type: str):
    """
        Day 1 核心功能：给轨道注入灵魂
        根据乐器类型设置不同的人性化参数
        - Drum: 稳重，小幅随机（Timing ±3, Velocity ±5）
        - Piano/Chords: 情感化，中幅随机（Timing ±10, Velocity ±15）
        - 其他: 默认（Timing ±5, Velocity ±8）
    """
    # 根据乐器类型配置参数
    if instrument_type == "drum":
        vel_variation = 5
        time_variation = 3
    elif instrument_type in ["piano", "synth_pad", "strings"]:
        vel_variation = 15
        time_variation = 10
    else:  # bass, synth_lead, fx
        vel_variation = 8
        time_variation = 5

    # 对每个音符进行随机化处理
    for note in track_data.notes:
        # 力度随机化（Velocity Humanization）
        velocity_change = random.randint(-vel_variation, vel_variation)
        note.velocity = max(1, min(127, note.velocity + velocity_change))

        # 时间微调（Timing Humanization）
        time_shift = random.randint(-time_variation, time_variation)
        note.start_time = max(0, note.start_time + time_shift)