# drums_agent_deepseek.py
import os, json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TPB = 480
BAR_TICKS = TPB * 4  # 4/4

GM_DRUM = {
    "kick": 36,        # Bass Drum 1
    "snare": 38,       # Acoustic Snare
    "clap": 39,        # Hand Clap
    "hat_closed": 42,  # Closed Hi-Hat
    "hat_open": 46,    # Open Hi-Hat
    "ride": 51,        # Ride Cymbal 1
}
# GM 鼓映射与 Channel 10 约定

class DrumsAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL"),
        )
        self.model = os.getenv("MODEL_NAME")

    def _call_json(self, system: str, user: str, max_tokens: int = 2000) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return json.loads(resp.choices[0].message.content)

    def generate_drums_for_section(self, blueprint: dict, section: dict) -> dict:
        # 你 blueprint.json 里 section.arrangement.drums 就是“导演指令”
        drums_inst = section["arrangement"].get("drums", {})
        section_len = section["end_bar"] - section["start_bar"] + 1

        system = (
            "You are a drum programmer for Deep House / EDM.\n"
            "Return JSON only (valid json).\n"
            "Use General MIDI drum notes and channel 10 convention (0-based channel=9).\n"
            "Rules:\n"
            "- pattern_len_bars must be 4 or 8.\n"
            "- notes are relative to pattern start: start_tick in [0, pattern_len_bars*bar_ticks).\n"
            "- Use GM pitches: kick=36 snare=38 closed_hat=42 open_hat=46 clap=39 ride=51.\n"
            "- Keep timing mostly on grid (1/16=120 ticks) but allow tiny variations only if asked.\n"
            "- Provide at least a core pattern; optionally a fill pattern for phrase endings.\n"
            "Return json now."
        )

        example = {
            "track_key": "drums",
            "section_name": "Drop",
            "section_start_bar": 17,
            "section_end_bar": 32,
            "pattern_len_bars": 4,
            "bar_ticks": BAR_TICKS,
            "patterns": [
                {
                    "tag": "full",
                    "notes": [
                        {"pitch": 36, "start_tick": 0, "duration_tick": 60, "velocity": 115},
                        {"pitch": 42, "start_tick": 0, "duration_tick": 60, "velocity": 90}
                    ]
                },
                {
                    "tag": "fill",
                    "notes": [
                        {"pitch": 38, "start_tick": BAR_TICKS*4-240, "duration_tick": 60, "velocity": 105}
                    ]
                }
            ],
            "phrases": [
                {"start_bar": 17, "end_bar": 24, "use_pattern_tag": "full"},
                {"start_bar": 25, "end_bar": 32, "use_pattern_tag": "full", "end_fill_tag": "fill"}
            ]
        }

        user = {
            "blueprint_global": {
                "bpm": blueprint["bpm"],
                "root_note": blueprint["root_note"],
                "scale": blueprint["scale"],
                "groove_global": blueprint.get("groove_global", {}),
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "global_energy": section["global_energy"],
                "chord_progression": section["chord_progression"],
                "chord_rhythm": section.get("chord_rhythm", "4bar"),
                "len_bars": section_len,
                "drums_instruction": drums_inst
            },
            "output_shape_example": example
        }

        return self._call_json(system, json.dumps(user, ensure_ascii=False), max_tokens=2400)

def summarize_kick_onsets(drum_pattern: dict, take_first_bars: int = 2) -> list[int]:
    """给 BassAgent 的摘要：只取前 N 小节 kick 的 start_tick"""
    pat_len = drum_pattern["pattern_len_bars"]
    max_tick = min(take_first_bars, pat_len) * BAR_TICKS
    # 找 core/full 里 kick
    kicks = []
    for p in drum_pattern.get("patterns", []):
        if p.get("tag") in ("core", "full", "light", "build"):
            for n in p.get("notes", []):
                if n["pitch"] == GM_DRUM["kick"] and n["start_tick"] < max_tick:
                    kicks.append(n["start_tick"])
            break
    return sorted(set(kicks))
