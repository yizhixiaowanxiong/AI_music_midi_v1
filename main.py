from dataclasses import dataclass
from typing import List
import mido
from mido import MidiFile,MidiTrack,Message
import random
import music_thoery


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
def generate_midi_file(track_data:Track,filename:str="output.midi"):
    # mid对象初始化
    mid = MidiFile()
    midi_track = MidiTrack()
    mid.tracks.append(midi_track)
    #轨道名称填充
    midi_track.append(mido.MetaMessage('track_name',name=track_data.name,time=0))
    # mid_track.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(120),time=0))
    events = []
    # 把 Note 对象拆解成 "Note On" (按下) 和 "Note Off" (松开) 事件
    # 写入到定义的空列表中
    for note in track_data.notes:
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
    #写入midi轨道
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
    mid.save(filename)
    print(f"成功生成midi文件: {filename}")

# 人性化力度和音符时间
def humanize_track(track_date : Track , vel_variation:int=10,time_variation:int=20):
    """
        给轨道注入灵魂：随机化力度和微调时间,修改数据流
    """
    for note in track_date.notes:
        # 力度便宜
        change=random.randint(-vel_variation,vel_variation)
        note.velocity=max(0,min(127,note.velocity+change))
        # 时间偏移
        time_shift = random.randint(-time_variation,time_variation)
        # 确保不会变为负数
        note.start_time=max(0,note.start_time+time_shift)
if __name__ == '__main__':
    TICKS_PER_BEAT = 480
    # 定义和弦进行 根音，Cmajor
    progression = [
        ("D", 4, "min7"),
        ("G", 3, "maj"),  # G3 Dom7 (这里简化用 maj)
        ("C", 4, "maj7"),
        ("C", 4, "maj7")  # 重复一小节
    ]
    generate_nodes = []
    current_time = 0
    for notename,octave,chord_type in progression:
        # 根音midi值
        root_midi=music_thoery.get_midi_note(notename,octave)
        # 和弦所有音符midi值
        chord_pitches=music_thoery.get_chord_notes(root_midi,chord_type)
        # 音符加入列表
        for pitch in chord_pitches:
            generate_nodes.append(Note(
                pitch=pitch,
                duration=TICKS_PER_BEAT * 4,  # 全音符
                velocity=90,  # 基础力度
                start_time=current_time
            ))
    # 下一个和弦在 4 拍后
    current_time += TICKS_PER_BEAT * 4
    # 2. 创建轨道
    lofi_track = Track(name="Lofi_Piano", notes=generate_nodes)
    # 3. 注入灵魂！(这一步是精髓)
    print("🤖 正在注入人性化 Groove...")
    humanize_track(lofi_track, vel_variation=15, time_variation=30)
    # 4. 生成文件
    generate_midi_file(lofi_track, "day2_lofi_humanized.mid")