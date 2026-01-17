# midi_renderer.py
from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo
from note_schema import TrackOut

TICKS_PER_BEAT = 480

def render_tracks_to_midi(tracks: list[TrackOut], bpm: int, out_path: str):
    mid = MidiFile(type=1)
    mid.ticks_per_beat = TICKS_PER_BEAT

    # global track
    gt = MidiTrack()
    mid.tracks.append(gt)
    gt.append(MetaMessage("set_tempo", tempo=bpm2tempo(bpm), time=0))

    for t in tracks:
        tr = MidiTrack()
        mid.tracks.append(tr)
        tr.append(MetaMessage("track_name", name=t.name, time=0))

        # 收集 note_on/off 事件并排序为 delta time
        events = []
        for n in t.notes:
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

    mid.save(out_path)  # mido save() :contentReference[oaicite:6]{index=6}
