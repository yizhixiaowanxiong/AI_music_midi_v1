# pipeline_minimal.py
import json
from director_agent import DirectorAgent
from agents.drums_agent import DrumsAgent
from agents.bass_agent import BassAgent
from summarize import extract_kick_onsets
from midi_renderer import render_tracks_to_midi

def main():
    # 1) Blueprint
    director = DirectorAgent()
    bp = director.generate_blueprint("写一首悲伤的 Deep House，总共 32 小节，C minor，包含 Intro 8、Build-up 8、Drop 16。")
    open("json_all/blueprint.json", "w", encoding="utf-8").write(bp.model_dump_json(indent=2, ensure_ascii=False))

    # 2) Drums (Drop)
    drums_agent = DrumsAgent()
    drums = drums_agent.generate(bp, target_section="Drop")
    kick_onsets = extract_kick_onsets(drums.track)


    # 3) Bass (reads kick_onsets)
    bass_agent = BassAgent()
    bass = bass_agent.generate(bp, kick_onsets=kick_onsets, target_section="Drop")

    # 4) Render to MIDI
    render_tracks_to_midi([drums.track, bass.track], bpm=bp.bpm, out_path="midi_all/demo_drums_bass.mid")

    print("✅ Generated: blueprint.json + demo_drums_bass.mid")
    print("👉 用 FL Studio 导入 MIDI（Piano roll -> MIDI Import）试听。")  # FL 导入 MIDI :contentReference[oaicite:7]{index=7}

if __name__ == "__main__":
    main()
