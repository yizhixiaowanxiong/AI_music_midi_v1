# summarize.py
from typing import List
from note_schema import TrackOut
# 
def extract_kick_onsets(track: TrackOut, kick_pitch: int = 36, max_events: int = 64) -> List[int]:
    onsets = [n.start_tick for n in track.notes if n.pitch == kick_pitch]
    onsets = sorted(set(onsets))
    return onsets[:max_events]
