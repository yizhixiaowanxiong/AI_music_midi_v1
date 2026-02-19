"""request.py - lightweight request/context schemas for track generation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.base import AgentRoutingRole, NoteEvent


class LastBarTransitionNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pitch_class: int
    start_tick: int


class LastBarMidiSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    track_key: str
    instrument: Optional[AgentRoutingRole | str] = None
    notes: List[LastBarTransitionNote] = Field(default_factory=list)
    bar_ticks: int


class PitchRange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    low_midi: int = Field(..., ge=0, le=127)
    high_midi: int = Field(..., ge=0, le=127)


class RhythmConstraint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    anchor_ticks: List[int] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)


class TrackContext(BaseModel):
    """
    Runtime context shared between tracks/sections.
    Keep it lightweight and plain-data only.
    """

    model_config = ConfigDict(extra="ignore")

    occupied_frequency_bands: List[str] = Field(default_factory=list)
    locked_rhythm_rules: List[str] = Field(default_factory=list)
    core_motif: Optional[str] = None

    occupied_pitch_ranges: List[PitchRange] = Field(default_factory=list)
    rhythm_constraints: List[RhythmConstraint] = Field(default_factory=list)

    kick_onsets_ticks: List[int] = Field(default_factory=list)
    chord_notes_per_bar: List[List[int]] = Field(default_factory=list)
    lyric_rhythm_ticks: List[int] = Field(default_factory=list)
    prev_section_last_bar_midi: List[LastBarMidiSummary] = Field(default_factory=list)

    def to_rule_text(self) -> str:
        parts: List[str] = []
        if self.occupied_frequency_bands:
            parts.append(f"Bands:{','.join(str(x) for x in self.occupied_frequency_bands)}")
        if self.locked_rhythm_rules:
            parts.append(f"Rhythm:{'; '.join(str(x) for x in self.locked_rhythm_rules[:4])}")
        if self.core_motif:
            parts.append(f"Motif:{str(self.core_motif)}")
        return " | ".join(parts)


class TrackGenRequest(BaseModel):
    """
    Flat work-order model for one track-generation task.
    Contains compatibility aliases used by graph + agents.
    """

    model_config = ConfigDict(extra="ignore")

    # identity/routing
    instrument: AgentRoutingRole | str = ""
    role: str = ""
    track_key: str = ""
    compute_layer: int = 0
    midi_channel: Optional[int] = None

    # section coordinates
    section_index: int
    section_name: str
    start_bar: int
    end_bar: int
    start_tick: int = 0
    end_tick: int = 0
    bar_ticks: int = 1920

    # music params
    time_signature: str = "4/4"
    bpm: int = 120
    key: str = "C"
    key_root: Optional[str] = None
    scale: str = "Minor"
    key_mode: Optional[str] = None

    chords: List[str] = Field(default_factory=list)
    chord_progression: List[str] = Field(default_factory=list)
    chord_rhythm: str = "4bar"

    energy: int = 3
    energy_level: Optional[float] = None

    # prompt context
    style_context: str = ""
    style_description: str = ""
    global_anchor_summary: str = ""
    section_summary: str = ""
    context_summary: str = ""
    context: Optional[TrackContext | Dict[str, Any]] = None

    # generation knobs
    strictness: int = 1
    temperature: float = 0.3
    design: Optional[Dict[str, Any]] = None
    extra_meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "TrackGenRequest":
        if not str(self.role or "").strip() and str(self.instrument or "").strip():
            value = getattr(self.instrument, "value", self.instrument)
            self.role = str(value or "")
        if not str(self.instrument or "").strip() and str(self.role or "").strip():
            self.instrument = str(self.role or "")

        if not str(self.key_root or "").strip():
            self.key_root = str(self.key or "")
        if not str(self.key or "").strip():
            self.key = str(self.key_root or "C")

        if not str(self.key_mode or "").strip():
            self.key_mode = str(self.scale or "")
        if not str(self.scale or "").strip():
            self.scale = str(self.key_mode or "Minor")

        if not self.chord_progression and self.chords:
            self.chord_progression = list(self.chords)
        if not self.chords and self.chord_progression:
            self.chords = list(self.chord_progression)

        if self.energy_level is None:
            self.energy_level = float(max(1, min(5, int(self.energy))) - 1) / 4.0

        if not str(self.style_description or "").strip() and str(self.style_context or "").strip():
            self.style_description = str(self.style_context)
        if not str(self.style_context or "").strip() and str(self.style_description or "").strip():
            self.style_context = str(self.style_description)

        if self.context is None:
            self.context = TrackContext()
        return self


class GeneratedTrack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    track_key: str
    instrument: AgentRoutingRole | str
    section_name: str
    channel: int = 0
    notes: List[NoteEvent] = Field(default_factory=list)
    raw_output: Optional[Any] = None
    metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
