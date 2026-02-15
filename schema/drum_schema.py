from __future__ import annotations

from schema.section_schema import Pattern, PatternNote, SectionOutputBase

# 兼容旧引用名，实际共用统一 Pattern/PatternNote 结构。
DrumNote = PatternNote
DrumPattern = Pattern


class DrumsSectionOutput(SectionOutputBase):
    track_key: str = "drums"
