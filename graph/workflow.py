"""Graph workflow wiring with concept/blueprint review gates."""

from __future__ import annotations

from typing import Any

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover
    END = "__end__"
    StateGraph = None  # type: ignore

try:
    from langgraph.checkpoint.memory import MemorySaver
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore

from graph.nodes import (
    blueprint_review_gate_node,
    concept_review_gate_node,
    generate_blueprint_node,
    generate_concept_node,
    generate_tracks_node,
    quality_gate_node,
    retry_failed_tracks_node,
    revise_blueprint_node,
    revise_concept_node,
    summarize_blueprint_node,
    summarize_concept_node,
)
from graph.state import MusicState


def _route_after_concept_review(state: MusicState) -> str:
    phase = str(state.get("current_phase", "")).strip().lower()
    if phase == "blueprint_build":
        return "to_blueprint"
    if phase == "concept_revise":
        return "to_revise"
    if phase == "concept_regenerate":
        return "to_regenerate"
    if phase == "aborted":
        return "to_end"
    return "to_wait"


def _route_after_blueprint_review(state: MusicState) -> str:
    phase = str(state.get("current_phase", "")).strip().lower()
    if phase == "production_build":
        return "to_tracks"
    if phase == "blueprint_revise":
        return "to_revise"
    if phase == "blueprint_regenerate":
        return "to_regenerate"
    if phase == "aborted":
        return "to_end"
    return "to_wait"


def _route_after_quality_gate(state: MusicState) -> str:
    phase = str(state.get("current_phase", "")).strip().lower()
    if phase == "track_retry":
        return "to_retry"
    return "to_end"


def build_music_graph() -> Any:
    if StateGraph is None:
        raise RuntimeError("langgraph is not installed. Please install langgraph first.")

    workflow = StateGraph(MusicState)

    # Concept stage
    workflow.add_node("define_concept", generate_concept_node)
    workflow.add_node("summarize_concept", summarize_concept_node)
    workflow.add_node("concept_review_gate", concept_review_gate_node)
    workflow.add_node("revise_concept", revise_concept_node)

    # Blueprint stage
    workflow.add_node("plan_structure", generate_blueprint_node)
    workflow.add_node("summarize_blueprint", summarize_blueprint_node)
    workflow.add_node("blueprint_review_gate", blueprint_review_gate_node)
    workflow.add_node("revise_blueprint", revise_blueprint_node)

    # Production stage
    workflow.add_node("compose_music", generate_tracks_node)

    workflow.set_entry_point("define_concept")

    workflow.add_edge("define_concept", "summarize_concept")
    workflow.add_edge("summarize_concept", "concept_review_gate")
    workflow.add_conditional_edges(
        "concept_review_gate",
        _route_after_concept_review,
        {
            "to_blueprint": "plan_structure",
            "to_revise": "revise_concept",
            "to_regenerate": "define_concept",
            "to_end": END,
            "to_wait": END,
        },
    )
    workflow.add_edge("revise_concept", "summarize_concept")

    workflow.add_edge("plan_structure", "summarize_blueprint")
    workflow.add_edge("summarize_blueprint", "blueprint_review_gate")
    workflow.add_conditional_edges(
        "blueprint_review_gate",
        _route_after_blueprint_review,
        {
            "to_tracks": "compose_music",
            "to_revise": "revise_blueprint",
            "to_regenerate": "plan_structure",
            "to_end": END,
            "to_wait": END,
        },
    )
    workflow.add_edge("revise_blueprint", "summarize_blueprint")

    # Quality gate + retry loop
    workflow.add_node("quality_gate", quality_gate_node)
    workflow.add_node("retry_failed_tracks", retry_failed_tracks_node)

    workflow.add_edge("compose_music", "quality_gate")
    workflow.add_conditional_edges(
        "quality_gate",
        _route_after_quality_gate,
        {
            "to_end": END,
            "to_retry": "retry_failed_tracks",
        },
    )
    workflow.add_edge("retry_failed_tracks", "quality_gate")

    return workflow


def build_graph() -> Any:
    graph = build_music_graph()
    kwargs: dict[str, Any] = {
        # Pause right before each review gate decision is consumed.
        "interrupt_after": ["summarize_concept", "summarize_blueprint"],
    }
    if MemorySaver is not None:
        kwargs["checkpointer"] = MemorySaver()
    return graph.compile(**kwargs)


try:
    music_graph_builder = build_music_graph()
except Exception:  # pragma: no cover
    music_graph_builder = None


try:
    music_app = build_graph()
except Exception:  # pragma: no cover
    music_app = None


__all__ = [
    "build_music_graph",
    "build_graph",
    "music_graph_builder",
    "music_app",
]

