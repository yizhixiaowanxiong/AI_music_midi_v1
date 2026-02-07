# midi_renderer.py
from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo
from track_builder import TrackOut

TICKS_PER_BEAT = 480


def render_tracks_to_midi(
    tracks: list[TrackOut],
    bpm: int,
    out_path: str,
    strictness: int = 1,
):
    """
    Render TrackOut list to a MIDI file.

    Args:
        tracks: Track list.
        bpm: Tempo.
        out_path: Output MIDI path.
        strictness: Reserved for future use (0=creative, 1=balanced, 2=stable).
    """
    mid = MidiFile(type=1)
    mid.ticks_per_beat = TICKS_PER_BEAT

    # global track
    gt = MidiTrack()
    mid.tracks.append(gt)
    gt.append(MetaMessage("set_tempo", tempo=bpm2tempo(bpm), time=0))

    for t in tracks:
        notes_to_render = t.notes

        tr = MidiTrack()
        mid.tracks.append(tr)
        tr.append(MetaMessage("track_name", name=t.name, time=0))

        # collect note_on/off and sort by absolute tick
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
