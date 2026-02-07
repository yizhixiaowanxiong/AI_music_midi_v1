# bass_agent.py
import os, json, re, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from schema.bass_schema import BassSectionOutput

load_dotenv()

TPB = 480

import copy


def _to_dict(obj):
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(x) for x in obj]
    if hasattr(obj, "model_dump"):
        return _to_dict(obj.model_dump())
    if hasattr(obj, "dict"):
        return _to_dict(obj.dict())
    return obj

def _find_json_start(text: str) -> int:
    if not text:
        return -1
    i_obj = text.find("{")
    i_arr = text.find("[")
    idxs = [i for i in (i_obj, i_arr) if i != -1]
    return min(idxs) if idxs else -1

def _extract_first_json_object(text: str) -> str:
    if not text:
        return ""
    start = text.find("{")
    if start == -1:
        return ""
    in_str = False
    escape = False
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""

def _clean_json_text(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    if "```" in text:
        pattern = r"```(?:json)?\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            text = match.group(1)
    extracted = _extract_first_json_object(text)
    return extracted or text

class BassAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL"),
        )
        self.model = os.getenv("MODEL_NAME")
        self._log_dir = Path("data/logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _call_json(self, system: str, user: str, max_tokens: int = 2000, temperature: float = 0.25) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content or ""
        self._log_raw_llm("bass_raw", content)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            cleaned = _clean_json_text(content)
            self._log_raw_llm("bass_cleaned", cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                start = _find_json_start(cleaned)
                if start != -1:
                    obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
                    return obj
                raise

    def _log_raw_llm(self, prefix: str, text: str) -> None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = self._log_dir / f"{prefix}_{ts}.txt"
        try:
            path.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def generate_bass_for_section(
        self,
        blueprint: dict,
        section: dict,
        drums_summary: dict,     # 你刚做的 summarize_drums_for_bass(..., mode="min") 的返回值
        strictness: int = 1,
        retries: int = 2,
    ) -> 'BassSectionOutput':
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)
        section_len = section["end_bar"] - section["start_bar"] + 1
        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = int(section.get("bar_ticks", 0) or 0)
        if not bar_ticks:
            n, d = time_signature.split("/")
            bar_ticks = int(TPB * int(n) * (4 / int(d)))

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
            "time_signature": time_signature,
            "ticks_per_beat": TPB,
            "bar_ticks": bar_ticks,
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

        prompt = json.dumps(user, ensure_ascii=False)

        last_err = None
        for i in range(retries + 1):
            data = self._call_json(system, prompt, max_tokens=2400, temperature=0.25 if i == 0 else 0.15)
            try:
                return BassSectionOutput(**data)
            except ValidationError as e:
                last_err = e
                fix_payload = copy.deepcopy(user)
                fix_payload["fix_instruction"] = "Fix ONLY invalid/missing fields. Keep all valid fields unchanged. Return FULL corrected JSON only."
                fix_payload["previous_validation_error"] = str(e)
                fix_payload["previous_json"] = data
                prompt = json.dumps(fix_payload, ensure_ascii=False)

        raise RuntimeError(f"Validation failed after retries: {last_err}")
