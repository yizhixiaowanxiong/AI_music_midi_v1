from dataclasses import dataclass
from typing import List
import mido
from mido import MidiFile,MidiTrack,Message
import random
import music_theory
from schema import ArrangementSchema,TrackSchema


@dataclass
# 音符类 描述单个 MIDI 音符
class Note:
    pitch : int #midi音高 0-127 C4=60
    duration : int #持续时间 以tick为单位
    velocity : int #力度0-127
    start_time : int #开始时间 绝对时间tick
@dataclass
# 音轨类 管理多个音符的集合
class Track:
    name : str #音轨名称，用于标识音轨用途
    notes : List[Note] #音符列表 管理音轨下的Note实例

# midi文件生成
def generate_midi_file(schema_data: ArrangementSchema,filename:str="output.midi"):
    """
        Service 层入口：接收 Agent 的数据对象，生成 MIDI 文件
    """
    mid = MidiFile()
    tempo = mido.bpm2tempo(schema_data.bpm)
    for schema_track in schema_data.tracks:
        midi_track = MidiTrack()
        mid.tracks.append(midi_track)
        #轨道名称填充
        midi_track.append(mido.MetaMessage('track_name',name=schema_track.name,time=0))
        midi_track.append(mido.MetaMessage('set_tempo',tempo=tempo,time=0))
        # 音符事件
        events = []
        # 把 Note 对象拆解成 "Note On" (按下) 和 "Note Off" (松开) 事件
        for note in schema_track.notes:
            events.append({
                "type": "note_on",
                "pitch": note.pitch,
                "velocity": note.velocity,
                "time": note.start_time
            })
            events.append({
                "type": "note_off",
                "pitch": note.pitch,
                "velocity": 0,
                "time": note.start_time + note.duration
            })
        # 按时间排序，匿名函数 lambda 提取每个字典 x 中 "time" 对应的值，并按其排序。
        events.sort(key=lambda x : x["time"])
        # 写入midi轨道
        last_time=0
        for event in events:
            #计算距离上一个事件过了多久
            delta_time = event["time"]-last_time
            midi_track.append(Message(
                type=event["type"],
                note=event["pitch"],
                velocity=event["velocity"],
                time=delta_time
            ))
            last_time=event["time"]
        print(f"   - 轨道 '{schema_track.name}' 写入了 {len(schema_track.notes)} 个音符")
    mid.save(filename)
    print(f"成功生成midi文件: {filename}")

# 人性化力度和音符时间
def humanize_track(track_data : Track , vel_variation:int=10,time_variation:int=20):
    """
        给轨道注入灵魂：随机化力度和微调时间,修改数据流
    """
    for note in track_data.notes:
        # 力度便宜
        change=random.randint(-vel_variation,vel_variation)
        note.velocity=max(0,min(127,note.velocity+change))
        # 时间偏移
        time_shift = random.randint(-time_variation,time_variation)
        # 确保不会变为负数
        note.start_time=max(0,note.start_time+time_shift)