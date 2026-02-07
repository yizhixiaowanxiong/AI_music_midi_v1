# drums_agent_deepseek.py (UPGRADED)
import os, json, re, time, ast
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError
from typing import List

from schema.drum_schema import DrumsSectionOutput, DrumNote

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
    # Prefer object extraction; if not found, return trimmed text for fallback parsing.
    extracted = _extract_first_json_object(text)
    return extracted or text

def _strip_trailing_commas(text: str) -> str:
    # Remove trailing commas before } or ]
    return re.sub(r",\s*([}\]])", r"\1", text)

def _try_parse_json(text: str):
    if not text:
        raise json.JSONDecodeError("Empty JSON", text, 0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_strip_trailing_commas(text))
    except json.JSONDecodeError:
        pass
    # As a last resort, try Python literal parsing (handles single quotes, True/False, None)
    try:
        obj = ast.literal_eval(text)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # Final attempt: apply trailing comma strip to literal_eval
    try:
        obj = ast.literal_eval(_strip_trailing_commas(text))
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # Give up
    raise json.JSONDecodeError("Failed to parse JSON after repairs", text, 0)

GM = {
    "kick": 36,
    "snare": 38,
    "clap": 39,
    "hat_closed": 42,
    "hat_open": 46,
    "ride": 51,
}


def _calc_bar_ticks(time_signature: str, tpb: int) -> int:
    n, d = time_signature.split("/")
    beats_per_bar = int(n)
    beat_unit = int(d)
    return int(tpb * beats_per_bar * (4 / beat_unit))

# claude
def check_four_on_floor(
    notes: List[DrumNote],
    bar_ticks: int,
    num_bars: int,
    time_signature: str = "4/4",
    ticks_per_beat: int = TPB,
) -> bool:
    """
    Check whether the kick pattern is four-on-the-floor (one kick per beat).
    """
    kicks = [n for n in notes if n.pitch == GM["kick"]]
    if not kicks:
        return False

    n, d = time_signature.split("/")
    beats_per_bar = int(n)
    beat_unit = int(d)
    beat_ticks = int(ticks_per_beat * (4 / beat_unit))

    expected_positions = []
    for bar in range(num_bars):
        for beat in range(beats_per_bar):
            expected_positions.append(bar * bar_ticks + beat * beat_ticks)

    kick_positions = {k.start_tick for k in kicks}

    matches = sum(1 for pos in expected_positions if pos in kick_positions)
    return matches >= len(expected_positions) * 0.75

# claude
def auto_correct_kick_pattern(
    notes: List[DrumNote],
    bar_ticks: int,
    num_bars: int,
    velocity: int = 115,
    time_signature: str = "4/4",
    ticks_per_beat: int = TPB,
) -> List[DrumNote]:
    """
    Auto-correct kick pattern to four-on-the-floor.
    """
    non_kick_notes = [n for n in notes if n.pitch != GM["kick"]]

    n, d = time_signature.split("/")
    beats_per_bar = int(n)
    beat_unit = int(d)
    beat_ticks = int(ticks_per_beat * (4 / beat_unit))

    new_kicks = []
    for bar in range(num_bars):
        for beat in range(beats_per_bar):
            start_tick = bar * bar_ticks + beat * beat_ticks
            new_kicks.append(DrumNote(
                pitch=GM["kick"],
                start_tick=start_tick,
                duration_tick=60,
                velocity=velocity
            ))

    return non_kick_notes + new_kicks

class DrumsAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL"),
        )
        self.model = os.getenv("MODEL_NAME")
        self._log_dir = Path("data/logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)
    # 大模型调用基础配置
    def _call_json(self, system: str, user: str, max_tokens: int = 1800, temperature: float = 0.25) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},  # JSON mode
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = resp.choices[0].message.content or ""
        self._log_raw_llm("drums_raw", content)
        try:
            return _try_parse_json(content)
        except json.JSONDecodeError:
            cleaned = _clean_json_text(content)
            self._log_raw_llm("drums_cleaned", cleaned)
            try:
                return _try_parse_json(cleaned)
            except json.JSONDecodeError:
                # Fallback: decode the first JSON value (object or array) and ignore trailing junk.
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
            # Avoid breaking tool execution due to logging failures.
            pass

    # 鼓组段落生成相关的类方法
    def generate_for_section(
        self,
        blueprint,
        section,
        strictness: int = 1,     # NEW: 软约束旋钮
        retries: int = 2
    ) -> DrumsSectionOutput:
        blueprint = _to_dict(blueprint)
        section = _to_dict(section)
        section_len = section["end_bar"] - section["start_bar"] + 1 # 当前章节的总小节数
        drums_inst = section["arrangement"].get("drums", {}) # 鼓组专属配置
        # CHANGED: 从 blueprint 读取拍号与TPB
        time_signature = section.get("time_signature") or blueprint.get("time_signature") or "4/4"
        bar_ticks = _calc_bar_ticks(time_signature, TPB)
        # -----------------------------
        # HARD RULES (non-negotiable)
        # -----------------------------
        system = (
            "You are a professional drum programmer.\n"
            "Return JSON only. No markdown.\n"
            "Hard rules (must follow):\n"
            f"- time_signature={time_signature}, ticks_per_beat={TPB}, bar_ticks={bar_ticks}.\n"
            "- Use GM pitches: kick=36, snare=38, clap=39, chh=42, ohh=46, ride=51.\n"
            "- pattern_len_bars must be one of: 1,2,4,8.\n"
            "- All note.start_tick must be within [0, pattern_len_bars*bar_ticks).\n"
            "- phrases must fully cover section_start_bar..section_end_bar and must not overlap.\n"
            "- phrases.use_pattern_tag / end_fill_tag must reference existing patterns[].tag.\n"
            "- Allowed pattern tags: mute,core,light,full,build,break,fill.\n"
            "\n"
            "CRITICAL: Fill generation strategy:\n"
            "- You MUST first create a 'core' or 'full' pattern as the main groove.\n"
            "- When creating a 'fill' pattern, you MUST base it on the core pattern's rhythmic skeleton.\n"
            "- Fill modifications allowed: add snare/tom rolls, add hat rolls, reduce kicks, add crash/ride accents.\n"
            "- Fill must maintain the same pattern_len_bars and general feel as core.\n"
            "- DO NOT create fills from scratch - always reference and modify the core groove.\n"
            "\n"
            "Soft goals (tunable by strictness):\n"
            "strictness 0=creative, 1=balanced, 2=stable.\n"
            "For dance genres, 'four-on-the-floor' is common but NOT mandatory unless strictness=2 and energy is high.\n"
            "Return JSON now."
        )

        example = {
            "track_key": "drums",
            "section_name": "Drop",
            "section_start_bar": 17,
            "section_end_bar": 32,
            "time_signature": time_signature,
            "pattern_len_bars": 4,
            "ticks_per_beat": TPB,
            "bar_ticks": bar_ticks,
            "patterns": [
                {"tag": "full", "notes": [
                    {"pitch": 36, "start_tick": 0, "duration_tick": 60, "velocity": 118}
                ]},
                {"tag": "fill", "notes": [
                    {"pitch": 38, "start_tick": 1920 * 4 - 240, "duration_tick": 60, "velocity": 110}
                ]}
            ],
            "phrases": [
                {"start_bar": 17, "end_bar": 24, "use_pattern_tag": "full"},
                {"start_bar": 25, "end_bar": 32, "use_pattern_tag": "full", "end_fill_tag": "fill"}
            ]
        }

        # -----------------------------
        # USER PAYLOAD: 把“软约束”显式化
        # -----------------------------
        user_payload = {
            "strictness": strictness,
            "song": {
                "bpm": blueprint.get("bpm"),
                "time_signature": time_signature,
                "ticks_per_beat": TPB,
                "bar_ticks": bar_ticks,
                "groove_global": blueprint.get("groove_global", {}),
                "style_description": blueprint.get("style_description", ""),
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "len_bars": section_len,
                "global_energy": section.get("global_energy", 0.5),
                "drums_instruction": drums_inst,
            },
            "example_shape": example
        }
        # json格式转换
        prompt = json.dumps(user_payload, ensure_ascii=False)
        # 容错机制
        last_err = None
        # 首次尝试（i=0）：temperature=0.25（中等随机性，保留一定创造力）
        # 重试时（i≥1）：temperature=0.15（低随机性，生成结果更稳定、更贴合指令，避免重试时仍出现大幅波动）
        for i in range(retries + 1):
            try:
                data = self._call_json(system, prompt, max_tokens=2200, temperature=0.25 if i == 0 else 0.15)
            except json.JSONDecodeError as e:
                last_err = e
                fix_payload = copy.deepcopy(user_payload)
                fix_payload["fix_instruction"] = (
                    "Your previous output was not valid JSON. "
                    "Return ONLY a single valid JSON object. "
                    "Use double quotes, include all required commas, and no extra text."
                )
                fix_payload["previous_json_error"] = str(e)
                prompt = json.dumps(fix_payload, ensure_ascii=False)
                continue
            try:
                output = DrumsSectionOutput(**data)

                # -----------------------------
                # POST-VALIDATION: Strictness 验证和自动修正
                # -----------------------------
                energy = section.get("global_energy", 0.5)

                # strictness=2 且 energy 高（>0.7）：强制 4-on-the-floor
                if strictness == 2 and energy > 0.7 and time_signature == "4/4":
                    # 检查 core/full 模式是否满足 4-on-the-floor
                    for pattern in output.patterns:
                        if pattern.tag in ("core", "full"):
                            if not check_four_on_floor(
                                pattern.notes,
                                bar_ticks,
                                output.pattern_len_bars,
                                time_signature=time_signature,
                                ticks_per_beat=TPB,
                            ):
                                pass
                                pattern.notes = auto_correct_kick_pattern(
                                    pattern.notes,
                                    bar_ticks,
                                    output.pattern_len_bars,
                                    time_signature=time_signature,
                                    ticks_per_beat=TPB,
                                )

                return output

            except ValidationError as e:
                last_err = e
                fix_payload = copy.deepcopy(user_payload)
                fix_payload["fix_instruction"] = "Fix ONLY invalid/missing fields. Keep all valid fields unchanged. Return FULL corrected JSON only."
                fix_payload["previous_validation_error"] = str(e)
                fix_payload["previous_json"] = data
                prompt = json.dumps(fix_payload, ensure_ascii=False)

        raise RuntimeError(f"Validation failed after retries: {last_err}")
