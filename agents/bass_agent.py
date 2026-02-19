"""agents/bass_agent.py"""
from agents.musician_agent import MusicianAgent
from utils.constants import MIDI_CHANNEL_BASS

class BassAgent(MusicianAgent):
    def __init__(self):
        super().__init__(instrument="bass", default_channel=MIDI_CHANNEL_BASS)