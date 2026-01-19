# midi_renderer.py
from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo
from note_schema import TrackOut
from humanize import humanize_track

TICKS_PER_BEAT = 480

def render_tracks_to_midi(
    tracks: list[TrackOut],
    bpm: int,
    out_path: str,
    enable_humanize: bool = True,
    strictness: int = 1
):
    """
    将轨道渲染为 MIDI 文件

    Args:
        tracks: 轨道列表
        bpm: 速度
        out_path: 输出文件路径
        enable_humanize: 是否启用人性化处理（默认 True）
        strictness: strictness 级别（0=creative, 1=balanced, 2=stable）
    """
    mid = MidiFile(type=1)
    mid.ticks_per_beat = TICKS_PER_BEAT

    # global track
    gt = MidiTrack()
    mid.tracks.append(gt)
    gt.append(MetaMessage("set_tempo", tempo=bpm2tempo(bpm), time=0))

    for t in tracks:
        # 渲染前处理：Humanize/Groove 层
        notes_to_render = t.notes
        if enable_humanize:
            notes_to_render = humanize_track(
                t.notes,
                track_key=t.instrument,
                strictness=strictness,
                ticks_per_beat=TICKS_PER_BEAT
            )

        tr = MidiTrack()
        mid.tracks.append(tr)
        tr.append(MetaMessage("track_name", name=t.name, time=0))

        # 收集 note_on/off 事件并排序为 delta time
        events = []
        for n in notes_to_render:
            events.append(("on", n.start_tick, n.pitch, n.velocity))
            events.append(("off", n.start_tick + n.duration_tick, n.pitch, 0))
        events.sort(key=lambda x: x[1])

        last = 0
        for kind, tick, pitch, vel in events:
            delta = max(0, tick - last)
            if kind == "on":
                tr.append(Message("note_on", channel=t.channel, note=pitch, velocity=vel, time=delta))
            else:
                tr.append(Message("note_off", channel=t.channel, note=pitch, velocity=0, time=delta))
            last = tick

    mid.save(out_path)
