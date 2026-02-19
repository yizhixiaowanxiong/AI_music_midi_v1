"""Graph state contract for concept -> blueprint -> role production flow."""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional, TypedDict

from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept
from schema.request import GeneratedTrack


def merge_tracks(old_tracks: Dict[str, GeneratedTrack], new_tracks: Dict[str, GeneratedTrack]) -> Dict[str, GeneratedTrack]:
    """Merge incremental track updates into the existing map."""
    if not old_tracks:
        return dict(new_tracks or {})
    if not new_tracks:
        return dict(old_tracks or {})
    return {**old_tracks, **new_tracks}


class ReviewPayload(TypedDict, total=False):
    approved: Optional[bool]
    action: Optional[str]  # accept | edit | regenerate | abort
    feedback: Optional[str]


class MusicState(TypedDict, total=False):
    # Runtime identity
    run_id: str
    session_id: str

    # User input
    user_request: str
    strictness: int

    # Core artifacts
    concept: Optional[SongConcept]
    blueprint: Optional[SongBlueprint]
    tracks: Annotated[Dict[str, GeneratedTrack], merge_tracks]

    # Summaries
    concept_summary_short: str
    concept_summary_long: str
    blueprint_summary_short: str
    blueprint_summary_long: str

    # Review contracts
    concept_review: ReviewPayload
    blueprint_review: ReviewPayload
    pending_user_action: Optional[str]  # concept_review | blueprint_review | None

    # Compatibility review fields (legacy callers)
    user_action: Optional[str]
    user_feedback: Optional[str]

    # Optional prompt anchor persisted in state
    global_anchor_summary: Optional[str]

    # Runtime status
    current_phase: str
    error: Optional[str]
    last_error_code: Optional[str]

    # Quality gate / track retry
    track_retry_count: int                    # times retried so far, starts at 0
    track_quality_issues: Dict[str, str]      # track_key -> issue description
    track_retry_requests: Dict[str, Any]      # track_key -> TrackGenRequest.model_dump()

