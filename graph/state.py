from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from schema.blueprint_schema import SongBlueprint
from schema.concept import SongConcept


def merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    return {**(a or {}), **(b or {})}


class MusicState(TypedDict):
    run_id: str
    session_id: str
    phase: str  # concept_draft|waiting_review|blueprint_build|section_generate|done|failed

    user_request: str
    concept_review: Optional[Dict[str, Any]]
    user_confirmed_duration_sec: Optional[float]

    concept: Optional[SongConcept]
    blueprint: Optional[SongBlueprint]
    global_anchor_summary: Optional[str]

    strictness: int
    tracks: Annotated[Dict[str, Any], merge_dicts]
    result_payload: Annotated[Dict[str, Any], merge_dicts]

    last_error: Optional[str]
    last_error_code: Optional[str]
    errors: Annotated[List[str], operator.add]
