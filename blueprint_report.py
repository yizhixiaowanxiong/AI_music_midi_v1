import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from schema.blueprint_schema import SongBlueprint


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _role_to_text(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _energy_from_concept(bp: SongBlueprint, section_name: str, section_index: int) -> Optional[float]:
    flow = getattr(bp.concept, "structure_flow", None) or []
    if 0 <= section_index < len(flow):
        return _float_or_none(flow[section_index].energy_curve)
    if 0 <= section_index - 1 < len(flow):
        return _float_or_none(flow[section_index - 1].energy_curve)
    for item in flow:
        if str(item.name).lower() == str(section_name).lower():
            return _float_or_none(item.energy_curve)
    return None


def _format_new_blueprint(bp: SongBlueprint) -> str:
    concept = bp.concept
    root = concept.scale.root if concept.scale else "N/A"
    scale = concept.scale.name if concept.scale else "N/A"

    lines = [
        f"Song: {concept.title}",
        f"Style: {concept.style_description}",
        f"BPM: {concept.bpm} | Key: {root} {scale} | Bars: {bp.total_bars} | TS: {concept.time_signature}",
        "-" * 90,
    ]

    for section in bp.sections:
        bars_count = section.bars_count or (section.end_bar - section.start_bar + 1)
        energy = _float_or_none(getattr(section, "global_energy", None))
        if energy is None:
            energy = _energy_from_concept(bp, section.name, section.index)
        energy_text = f"{energy:.2f}" if energy is not None else "n/a"
        chord_str = " ".join(section.chord_progression)

        lines.append(
            f"[{section.name:<10}] bars {section.start_bar:>2}-{section.end_bar:<2} "
            f"(len={bars_count:>2})  energy={energy_text}  chord_rhythm={section.chord_rhythm}"
        )
        lines.append(f"  chords: {chord_str}")
        for track_key, inst in section.arrangement.items():
            role = _role_to_text(inst.role)
            mix = f" | mix={inst.mixing_hint}" if inst.mixing_hint else ""
            lines.append(
                f"  - {track_key:<8} role={role:<10} inst={inst.instrument_name} | "
                f"{inst.playing_style}{mix}"
            )
        lines.append(f"  transition: {section.transition_to_next}")
        lines.append("")

    lines.append("QC: Pydantic validation passed")
    return "\n".join(lines)


def _format_legacy_blueprint(data: Dict[str, Any]) -> str:
    lines = [
        f"Song: {data.get('song_name', 'Unknown')}",
        f"Style: {data.get('style_description', 'Unknown')}",
        (
            f"BPM: {data.get('bpm', 'n/a')} | "
            f"Key: {data.get('root_note', 'n/a')} {data.get('scale', 'n/a')} | "
            f"Bars: {data.get('total_bars', 'n/a')} | "
            f"TS: {data.get('time_signature', 'n/a')}"
        ),
        "-" * 90,
    ]

    for section in data.get("sections", []):
        start = int(section.get("start_bar", 1))
        end = int(section.get("end_bar", start))
        bars_count = end - start + 1
        energy = _float_or_none(section.get("global_energy"))
        energy_text = f"{energy:.2f}" if energy is not None else "n/a"
        chord_str = " ".join(section.get("chord_progression", []))
        lines.append(
            f"[{section.get('name', 'Section'):<10}] bars {start:>2}-{end:<2} "
            f"(len={bars_count:>2})  energy={energy_text}  "
            f"chord_rhythm={section.get('chord_rhythm', 'n/a')}"
        )
        lines.append(f"  chords: {chord_str}")
        for track_key, inst in (section.get("arrangement") or {}).items():
            lines.append(
                f"  - {track_key:<8} role={inst.get('role', 'n/a'):<10} "
                f"var={inst.get('variant_tag', 'n/a'):<5} "
                f"e={_float_or_none(inst.get('energy_level')) or 0.0:.2f} | "
                f"{inst.get('playing_style', 'n/a')}"
            )
        lines.append("")

    lines.append("QC: Legacy blueprint loaded")
    return "\n".join(lines)


def format_report(bp_or_dict: Union[SongBlueprint, Dict[str, Any]]) -> str:
    if isinstance(bp_or_dict, SongBlueprint):
        return _format_new_blueprint(bp_or_dict)

    if isinstance(bp_or_dict, dict):
        if "concept" in bp_or_dict and "sections" in bp_or_dict:
            try:
                validated = SongBlueprint.model_validate(bp_or_dict)
                return _format_new_blueprint(validated)
            except Exception:
                pass
        return _format_legacy_blueprint(bp_or_dict)

    raise TypeError(f"Unsupported blueprint type: {type(bp_or_dict)!r}")


def print_report(bp_or_dict: Union[SongBlueprint, Dict[str, Any]]) -> None:
    print(format_report(bp_or_dict))


if __name__ == "__main__":
    path = Path("data/json_all/blueprint.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    print_report(payload)
