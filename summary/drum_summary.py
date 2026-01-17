from schema.drum_schema import DrumsSectionOutput


def summarize_drums_for_bass(drums_out: DrumsSectionOutput, prefer_tag=("full","core","build","light")) -> dict:
    bar_ticks = drums_out.bar_ticks
    pat_ticks = drums_out.pattern_len_bars * bar_ticks

    chosen = None
    for tag in prefer_tag:
        for p in drums_out.patterns:
            if p.tag == tag:
                chosen = p
                break
        if chosen: break
    if not chosen and drums_out.patterns:
        chosen = drums_out.patterns[0]

    kick = sorted({n.start_tick for n in chosen.notes if n.pitch == 36})
    snare_or_clap = sorted({n.start_tick for n in chosen.notes if n.pitch in (38,39)})

    return {
        "section": drums_out.section_name,
        "pattern_len_bars": drums_out.pattern_len_bars,
        "pattern_ticks": pat_ticks,
        "kick_onsets": kick[:32],          # 只取前 N 个，够用了
        "backbeat_onsets": snare_or_clap[:16],
    }
