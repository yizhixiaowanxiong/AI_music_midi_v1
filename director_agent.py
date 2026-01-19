# director_agent_deepseek.py
import os, json
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from blueprint_schema import SongBlueprint

load_dotenv()

class DirectorAgent:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL"),
        )
        self.model = os.getenv("MODEL_NAME")
    # 大模型调用强制返回JSON数据格式结果的工具函数
    def _call_llm_json(self, system: str, user: str, max_tokens: int = 2000) -> dict:
        # DeepSeek: JSON Output、
        # 模型调用配置
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},  # ✅ 强制 DeepSeek 模型仅输出合法 JSON 字符串
            temperature=0.2, #低随机性
            max_tokens=max_tokens, #最大token量
        )
        content = resp.choices[0].message.content #从响应resp里面提取第一个候选结果
        return json.loads(content) #讲json字符串解析为python字典格式

    def generate_blueprint(self, user_prompt: str, retries: int = 2) -> SongBlueprint:
        system = (
            "You are a music Director. Output JSON only.\n"
            "IMPORTANT: output must be valid json.\n"
            "Rules:\n"
            "- Only output a JSON object, no markdown.\n"
            "- sections must fully cover 1..total_bars with no overlap.\n"
            "- Must include Drop and Drop has the highest global_energy.\n"
            "- chord_progression is a list of chord symbols.\n"
            "- You MUST set chord_rhythm + repeat so that:\n"
            "  section_len_bars == len(chord_progression) * bars_per_chord(chord_rhythm) * repeat\n"
            "- If section_len_bars >= 16, you MUST provide phrases covering the whole section, typically 8-bar phrases.\n"
            "- phrase arrangement_override uses VariantTag only.\n"
            "- arrangement keys must be one of: drums,bass,chords,pad,lead,fx,vocal.\n"
            "- role must be one of: silent,background,support,lead,solo.\n"
            "- variant_tag must be one of: mute,core,light,full,build,break,fill.\n"
            "- groove_global.feel must be one of: tight,neutral,laid_back.\n"
            "Return json now."
        )

        # 给一个很小的 JSON 样例，降低乱输出概率
        example = {
            "song_name": "string",
            "style_description": "string",
            "bpm": 120,
            "time_signature": "4/4",
            "root_note": "C",
            "scale": "minor",
            "total_bars": 32,
            "groove_global": {"humanize_level": "mid", "swing": "mid", "feel": "neutral"},
            "sections": [
            {
              "name":"Drop",
              "start_bar":17,
              "end_bar":32,
              "global_energy":0.9,
              "chord_progression":["Cm","Ab","Eb","Bb"],
              "chord_rhythm":"1bar",
              "progression_is_loop": True,
              "repeat": 4,   # 和弦循环 4 次，刚好覆盖 16 小节
               # 用最小可用 dict 替代 { ... }
              "arrangement": {
                "drums": {"role": "lead", "playing_style": "four-on-floor", "energy_level": 0.9, "variant_tag": "full"},
                "bass":  {"role": "support", "playing_style": "syncopated",    "energy_level": 0.7, "variant_tag": "full"},
                "pad":   {"role": "support", "playing_style": "stabs",         "energy_level": 0.6, "variant_tag": "full"},
                "fx":    {"role": "background", "playing_style": "risers",     "energy_level": 0.4, "variant_tag": "build"},
                "lead":  {"role": "solo", "playing_style": "melancholic",      "energy_level": 0.6, "variant_tag": "core"},
                "chords":{"role": "support", "playing_style": "chord stabs",   "energy_level": 0.6, "variant_tag": "full"}
              },
              "phrases":[
                {"start_bar":17,"end_bar":24,"arrangement_override":{"drums":"full","bass":"full","pad":"full"}},
                {"start_bar":25,"end_bar":32,"arrangement_override":{"drums":"full","bass":"full","pad":"full","fx":"fill","lead":"core"}}
              ]
            }
            ]
            }


        user = (
            f"User request: {user_prompt}\n\n"
            f"Here is an example JSON shape (follow this shape):\n{json.dumps(example, ensure_ascii=False)}"
        )
        # 记录最后一次验证错误
        last_err = None
        # 重试循环
        for i in range(retries + 1):
            # 调用大模型生成json格式数据
            data = self._call_llm_json(system, user, max_tokens=2400)

            try:
                # 你 schema 里已经做了 normalize + QC，这里直接验证
                return SongBlueprint(**data)
            except ValidationError as e:
                last_err = e
                # 把错误摘要给模型，让它“只修错字段”
                user = (
                    "The previous json has schema errors. Fix ONLY the incorrect fields and return full corrected json.\n"
                    "Remember: output valid json only.\n\n"
                    f"Errors:\n{e}\n\n"
                    f"Previous json:\n{json.dumps(data, ensure_ascii=False)}"
                )

        raise last_err

if __name__ == "__main__":
    agent = DirectorAgent()
    prompt = "写一首悲伤的 Deep House，总共 32 小节，C minor，包含 Intro 8、Build-up 8、Drop 16。"
    bp = agent.generate_blueprint(prompt)
    open("json_all/blueprint.json", "w", encoding="utf-8").write(bp.model_dump_json(indent=2, ensure_ascii=False))
    print("✅ blueprint.json 已生成")
