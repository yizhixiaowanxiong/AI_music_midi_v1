from typing import List, Dict

# 12平均律音名映射 (为了以后 LLM 输入 "C#4" 能转成 MIDI 61)
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
# 音阶间隔 (半音数)
# Major: 全 全 半 全 全 全 半 (2, 2, 1, 2, 2, 2, 1)
SCALE_INTERVALS = {
    "major": [0, 2, 4, 5, 7, 9, 11],#自然大调
    "minor": [0, 2, 3, 5, 7, 8, 10], # 自然小调
    "dorian": [0, 2, 3, 5, 7, 9, 10], # 赛博朋克/爵士常用
    "phrygian": [0, 1, 3, 5, 7, 8, 10] # 黑暗/Trap 风格常用
}
# 和弦构成 (相对于根音的半音偏移)
CHORD_TYPES = {
    "maj": [0, 4, 7],       # 大三和弦
    "min": [0, 3, 7],       # 小三和弦
    "maj7": [0, 4, 7, 11],  # 大七和弦 (Lo-Fi 必备)
    "min7": [0, 3, 7, 10]   # 小七和弦
}
# 输入：音符名，八度；
# 函数作用：将音名 / 八度转换为 MIDI 音编号：0-127
def get_midi_note(note_name:str,octave:int)->int:
    try:
        # 在12平均律中找索引，然后转换midi：C4 对应 MIDI 60
        base_idx=NOTE_NAMES.index(note_name)
        return (octave+1)*12+base_idx
    except ValueError:
        raise ValueError(f"未知的音名：{note_name}")
# 根据根音，和和弦类型 生成和弦内所有音的 MIDI 编号
def get_chord_notes(root_note:int,chord_type:str)-> List[int]:
    # 输入类型转化小写
    intervals=CHORD_TYPES.get(chord_type.lower())
    # 获取和弦音程列表遍历输出对应编号
    if not intervals:
        raise ValueError(f"不支持和弦的类型:{chord_type}")
    return [root_note+interval for interval in intervals]