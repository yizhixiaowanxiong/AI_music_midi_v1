# drums_agent_deepseek.py (UPGRADED)
import os, json
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

# CHANGED: 导入路径以你的项目结构为准
from schema.drum_schema import DrumsSectionOutput

load_dotenv()

DEFAULT_TPB = 480

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
        tpb = blueprint.get("ticks_per_beat", 480)
        bar_ticks = _calc_bar_ticks(time_signature, tpb)
        # -----------------------------
        # HARD RULES (non-negotiable)
        # -----------------------------
        system = (
            "You are a professional drum programmer.\n"
            "Return JSON only. No markdown.\n"
            "Hard rules (must follow):\n"
            f"- time_signature={time_signature}, ticks_per_beat={tpb}, bar_ticks={bar_ticks}.\n"
            "- Use GM pitches: kick=36, snare=38, clap=39, chh=42, ohh=46, ride=51.\n"
            "- pattern_len_bars must be one of: 1,2,4,8.\n"
            "- All note.start_tick must be within [0, pattern_len_bars*bar_ticks).\n"
            "- phrases must fully cover section_start_bar..section_end_bar and must not overlap.\n"
            "- phrases.use_pattern_tag / end_fill_tag must reference existing patterns[].tag.\n"
            "- Allowed pattern tags: mute,core,light,full,build,break,fill.\n"
            "\n"
            "Soft goals (tunable):\n"
            "strictness 0=creative, 1=balanced, 2=stable.\n"
            "For dance genres, 'four-on-the-floor' is common but NOT mandatory unless strictness=2 and energy is high.\n"
            "Return JSON now."
        )

        example = {
            "track_key": "drums",
            "section_name": "Drop",
            "section_start_bar": 17,
            "section_end_bar": 32,
            "strictness": 1,
            "pattern_len_bars": 4,
            "ticks_per_beat": 480,
            "bar_ticks": 1920,
            "patterns": [
                {"tag":"full","notes":[
                    {"pitch":36,"start_tick":0,"duration_tick":60,"velocity":118}
                ]},
                {"tag":"fill","notes":[
                    {"pitch":38,"start_tick":1920*4-240,"duration_tick":60,"velocity":110}
                ]}
            ],
            "phrases":[
                {"start_bar":17,"end_bar":24,"use_pattern_tag":"full"},
                {"start_bar":25,"end_bar":32,"use_pattern_tag":"full","end_fill_tag":"fill"}
            ]
        }

        # -----------------------------
        # USER PAYLOAD: 把“软约束”显式化
        # -----------------------------
        user_payload = {
            # 全局约束旋钮
            "strictness": strictness,
            # 音乐基础信息
            "song": {
                "bpm": blueprint.get("bpm"),
                "style_description": blueprint.get("style_description",""),
                "groove_global": blueprint.get("groove_global", {}),
            },
            "section": {
                "name": section["name"],
                "start_bar": section["start_bar"],
                "end_bar": section["end_bar"],
                "len_bars": section_len,
                "global_energy": section.get("global_energy", 0.5),
                "drums_instruction": drums_inst,  # 来自 Director 的鼓指挥
            },
            # 软偏好引导
            "soft_preferences": {
                "intro_kick_density_max": 0.2 if strictness >= 1 else 0.5,   # 不是禁止，是比例上限
                "drop_four_on_floor_weight": 0.9 if strictness >= 2 else 0.5,
                "allow_breakbeat_probability": 0.15 if strictness == 0 else 0.05,
                "fill_per_8bars_target": 1 if strictness >= 1 else 0,
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
                return DrumsSectionOutput(**data)
            except ValidationError as e:
                last_err = e
                prompt = (
                    "Fix the JSON to match schema/enums/validators. "
                    "Keep musical intent but correct invalid fields. Return FULL corrected JSON only.\n\n"
                    f"Errors:\n{e}\n\nPrevious JSON:\n{json.dumps(data, ensure_ascii=False)}"
                )

        raise last_err
