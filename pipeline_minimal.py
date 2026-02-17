import asyncio
import json
from pathlib import Path

from graph.orchestrator import SongOrchestrator
from utils.duration import default_duration_from_concept
from midi_renderer import render_tracks_to_midi
from schema.concept import SongConcept
from track_builder import bass_to_track, drums_to_track


async def main():
    user_request = (
        "Write a melancholic deep house track, 32 bars total, C minor, "
        "with Intro 8, Build-up 8, Drop 16."
    )

    orchestrator = SongOrchestrator()
    status = await orchestrator.start_run(user_request=user_request, strictness=1)
    run_id = status["run_id"]
    concept = SongConcept.model_validate(status.get("concept") or {})
    duration = default_duration_from_concept(concept)

    await orchestrator.submit_concept_review(
        approved=True,
        user_confirmed_duration_sec=duration,
        run_id=run_id,
    )
    result = await orchestrator.get_run_result(run_id=run_id)
    if not result.get("ready"):
        raise RuntimeError(
            f"Run not ready. phase={result.get('phase')} error={result.get('last_error')}"
        )

    blueprint_payload = result.get("blueprint") or {}
    out_json = Path("data/json_all/blueprint.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(blueprint_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    tracks_by_section = result.get("tracks") or {}
    first_section_key = next(iter(tracks_by_section.keys()), None)
    if not first_section_key:
        raise RuntimeError("No tracks were generated.")

    tracks = tracks_by_section[first_section_key]
    midi_tracks = []
    for track in tracks:
        role = str(track.get("instrument", "")).lower()
        raw = track.get("raw_output")
        if raw is None:
            continue
        if role in ("percussion", "drums"):
            midi_tracks.append(drums_to_track(raw))
        elif role == "bass":
            midi_tracks.append(bass_to_track(raw))

    if midi_tracks:
        bpm = int(((blueprint_payload.get("concept") or {}).get("bpm")) or 120)
        render_tracks_to_midi(
            tracks=midi_tracks,
            bpm=bpm,
            out_path="data/midi_all/demo_drums_bass.mid",
            strictness=1,
        )

    print("Generated: blueprint.json + demo_drums_bass.mid")
    print("Backend: langgraph")
    print(f"Run ID: {run_id}")
    print(f"Concept: {concept.title}")


if __name__ == "__main__":
    asyncio.run(main())
