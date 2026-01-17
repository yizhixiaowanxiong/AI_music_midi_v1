# blueprint_report.py
import json
from blueprint_schema import SongBlueprint

def print_report(bp: SongBlueprint):
    print(f"🎵 {bp.song_name}")
    print(f"Style: {bp.style_description}")
    print(f"BPM: {bp.bpm} | Key: {bp.root_note} {bp.scale} | Bars: {bp.total_bars} | TS: {bp.time_signature}")
    print("-" * 90)
    # 段落详情打印
    # 段落名称，起止小节+长度，全局能量值，和弦节奏类型
    for s in bp.sections:
        ln = s.end_bar - s.start_bar + 1
        chord_str = " ".join(s.chord_progression)
        print(f"[{s.name:<10}] bars {s.start_bar:>2}-{s.end_bar:<2} (len={ln:>2})  energy={s.global_energy:.2f}  chord_rhythm={s.chord_rhythm}")
        print(f"  chords: {chord_str}")
        for k, inst in s.arrangement.items():
            print(f"  - {k:<6} role={inst.role:<10} var={inst.variant_tag:<5} e={inst.energy_level:.2f} | {inst.playing_style}")
        print()

    print("✅ QC Passed（Pydantic 校验已通过）")

if __name__ == "__main__":
    bp = SongBlueprint(**json.load(open("blueprint.json", "r", encoding="utf-8")))
    print_report(bp)
