# drums_agent_deepseek.py (UPGRADED)
import os, json
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError
from typing import List

from schema.drum_schema import DrumsSectionOutput, DrumNote

load_dotenv()

TPB = 480

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
def check_four_on_floor(notes: List[DrumNote], bar_ticks: int, num_bars: int) -> bool:
    """
    检查是否满足 4-on-the-floor（每拍一个 kick）

    Args:
        notes: 音符列表
        bar_ticks: 每小节的 tick 数
        num_bars: 小节数

    Returns:
        True 如果满足 4-on-the-floor
    """
    kicks = [n for n in notes if n.pitch == GM["kick"]]
    if not kicks:
        return False

    # 对于 4/4 拍，期望每拍（480 ticks）有一个 kick
    # 即在 0, 480, 960, 1440 ... 位置
    expected_positions = []
    for bar in range(num_bars):
        for beat in range(4):  # 4/4 拍
            expected_positions.append(bar * bar_ticks + beat * TPB)

    kick_positions = {k.start_tick for k in kicks}

    # 至少 75% 的期望位置有 kick（允许一定灵活性）
    matches = sum(1 for pos in expected_positions if pos in kick_positions)
    return matches >= len(expected_positions) * 0.75

# claude
def auto_correct_kick_pattern(
    notes: List[DrumNote],
    bar_ticks: int,
    num_bars: int,
    velocity: int = 115
) -> List[DrumNote]:
    """
    自动修正 kick pattern 为 4-on-the-floor

    Args:
        notes: 原始音符列表
        bar_ticks: 每小节的 tick 数
        num_bars: 小节数
        velocity: kick 的力度

    Returns:
        修正后的音符列表（包含修正的 kick 和原有的其他音符）
    """
    # 移除所有现有的 kick
    non_kick_notes = [n for n in notes if n.pitch != GM["kick"]]

    # 添加标准 4-on-the-floor kick
    new_kicks = []
    for bar in range(num_bars):
        for beat in range(4):  # 4/4 拍
            start_tick = bar * bar_ticks + beat * TPB
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
    # 大模型调用基础配置
    def _call_json(self, system: str, user: str, max_tokens: int = 1800) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            response_format={"type":"json_object"},  # JSON mode
            temperature=0.25,
            max_tokens=max_tokens,
        )
        return json.loads(resp.choices[0].message.content)

    # 鼓组段落生成相关的类方法
    def generate_for_section(
        self,
        blueprint: dict,
        section: dict,
        strictness: int = 1,     # NEW: 软约束旋钮
        retries: int = 2
    ) -> DrumsSectionOutput:
        section_len = section["end_bar"] - section["start_bar"] + 1 # 当前章节的总小节数
        drums_inst = section["arrangement"].get("drums", {}) # 鼓组专属配置
        # CHANGED: 从 blueprint 读取拍号与TPB
        time_signature = blueprint.get("time_signature", "4/4")
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
            "time_signature": "4/4",
            "pattern_len_bars": 4,
            "ticks_per_beat": 480,
            "bar_ticks": 1920,
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
        for _ in range(retries + 1):
            data = self._call_json(system, prompt, max_tokens=2200)
            try:
                output = DrumsSectionOutput(**data)

                # -----------------------------
                # POST-VALIDATION: Strictness 验证和自动修正
                # -----------------------------
                energy = section.get("global_energy", 0.5)

                # strictness=2 且 energy 高（>0.7）：强制 4-on-the-floor
                if strictness == 2 and energy > 0.7:
                    # 检查 core/full 模式是否满足 4-on-the-floor
                    for pattern in output.patterns:
                        if pattern.tag in ("core", "full"):
                            if not check_four_on_floor(pattern.notes, bar_ticks, output.pattern_len_bars):
                                print(f"⚠️  Strictness=2: Auto-correcting {pattern.tag} pattern to 4-on-the-floor")
                                pattern.notes = auto_correct_kick_pattern(
                                    pattern.notes,
                                    bar_ticks,
                                    output.pattern_len_bars
                                )

                return output

            except ValidationError as e:
                last_err = e
                prompt = (
                    "Fix the JSON to match schema/enums/validators. "
                    "Keep musical intent but correct invalid fields. Return FULL corrected JSON only.\n\n"
                    f"Errors:\n{e}\n\nPrevious JSON:\n{json.dumps(data, ensure_ascii=False)}"
                )

        raise last_err
