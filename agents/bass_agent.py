# bass_agent.py
import os, json
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from schema.bass_schema import BassSectionOutput

load_dotenv()

TPB = 480

class BassAgent:
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
            temperature=0.25,
            max_tokens=max_tokens,
        )
        return json.loads(resp.choices[0].message.content)

    def generate_bass_for_section(
        self,
        blueprint: dict,
        section: dict,
        drums_summary: dict,     # 你刚做的 summarize_drums_for_bass(..., mode="min") 的返回值
        strictness: int = 1,
    ) -> dict:
        section_len = section["end_bar"] - section["start_bar"] + 1

        system = (
            "You are a Deep House / Tech House bassline writer.\n"
            "Return JSON only (valid json).\n"
            "Goal: tight low-end with the drums.\n"
            "Rules:\n"
            "- Output must match the given output schema example.\n"
            "- pattern_len_bars in {1,2,4,8}; prefer 4.\n"
            "- notes are relative to pattern start: start_tick in [0, pattern_len_bars*bar_ticks).\n"
            "- Keep bass mostly on grid (1/16) with short notes; allow longer notes on break bars.\n"
            "- IMPORTANT: Use the provided Kick Summary to avoid placing strong bass attacks exactly on kick steps.\n"
            "- On Kick Break bars (no kick), bass may sustain or do a simple riff.\n"
            "- Prefer chord tones (root/5th/3rd) of the current chord progression.\n"
            "Return json now."
        )

        # 给 LLM 一个清晰的“长什么样”
        example = {
            "track_key": "bass",
            "section_name": "Drop",
            "section_start_bar": 17,
            "section_end_bar": 32,
            "time_signature": "4/4",
            "ticks_per_beat": 480,
            "bar_ticks": 1920,
            "strictness": 1,
            "pattern_len_bars": 4,
            "patterns": [
                {
                    "tag": "core",
                    "notes": [
                        {"pitch": 36, "start_tick": 240, "duration_tick": 120, "velocity": 105},  # C2
                        {"pitch": 36, "start_tick": 720, "duration_tick": 120, "velocity": 100},
                    ],
                },
                {
                    "tag": "break",
                    "notes": [
                        {"pitch": 36, "start_tick": 0, "duration_tick": 1920, "velocity": 90}
                    ],
                }
            ],
            "phrases": [
                {"start_bar": 17, "end_bar": 28, "use_pattern_tag": "core"},
                {"start_bar": 29, "end_bar": 32, "use_pattern_tag": "core"}
            ]
        }

        user = {
            "blueprint_global": {
                "bpm": blueprint["bpm"],
                "root_note": blueprint["root_note"],
                "scale": blueprint["scale"],
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "len_bars": section_len,
                "global_energy": section.get("global_energy", 0.7),
                "chord_progression": section.get("chord_progression", []),
                "chord_rhythm": section.get("chord_rhythm", "4bar"),
            },
            "drums_kick_summary_for_listening": drums_summary.get("llm_context_text", ""),
            "strictness": strictness,
            "output_shape_example": example,
        }

        data = self._call_json(system, json.dumps(user, ensure_ascii=False), max_tokens=2400)

        # 可选：用 pydantic 校验一下，坏了就直接抛错（你调 prompt 时很有用）
        try:
            _ = BassSectionOutput(**data)
        except ValidationError as e:
            data["_validation_error"] = str(e)

        return data
