"""agents/drums_agent.py"""
from agents.musician_agent import MusicianAgent
from utils.constants import MIDI_CHANNEL_DRUMS

class DrumsAgent(MusicianAgent):
    def __init__(self):
        super().__init__(instrument="drums", default_channel=MIDI_CHANNEL_DRUMS)