# pipeline_minimal.py
from agents.director_agent import DirectorAgent
from agents.drums_agent import DrumsAgent
from agents.bass_agent import BassAgent
from summary.drum_summary import summarize_drums_for_bass
from track_builder import drums_to_track, bass_to_track
from midi_renderer import render_tracks_to_midi

def main():
    # 1) Blueprint
    director = DirectorAgent()
    bp = director.generate_blueprint("写一首悲伤的 Deep House，总共 32 小节，C minor，包含 Intro 8、Build-up 8、Drop 16。")
    open("data/json_all/blueprint.json", "w", encoding="utf-8").write(bp.model_dump_json(indent=2, ensure_ascii=False))

    # 2) Drums (Drop)
    drums_agent = DrumsAgent()
    drums = drums_agent.generate_for_section(bp, bp.sections[2])
    drum_summary = summarize_drums_for_bass(drums, mode="min")


    # 3) Bass (reads kick summary)
    bass_agent = BassAgent()
    bass = bass_agent.generate_bass_for_section(bp, bp.sections[2], drum_summary)

    # 4) Render to MIDI
    drums_track = drums_to_track(drums)
    bass_track = bass_to_track(bass)
    render_tracks_to_midi([drums_track, bass_track], bpm=bp.bpm, out_path="data/midi_all/demo_drums_bass.mid")

    print("✅ Generated: blueprint.json + demo_drums_bass.mid")
    print("👉 用 FL Studio 导入 MIDI（Piano roll -> MIDI Import）试听。")  # FL 导入 MIDI :contentReference[oaicite:7]{index=7}

if __name__ == "__main__":
    main()
