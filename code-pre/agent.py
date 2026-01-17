import os
import time
import traceback
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from schema import ArrangementSchema
from service import export_midi  # 你上一步改写后的 service.py 里应该有 export_midi(payload, out_path,...)

# Windows 控制台 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()


def build_json_schema_for_openai() -> dict:
    """
    OpenAI structured outputs 的 json_schema 包装
    """
    return {
        "name": "ArrangementSchema",
        "schema": ArrangementSchema.model_json_schema(),
        "strict": True,
    }


SYSTEM_PROMPT = """
你是一位精通 FL Studio 的电子音乐制作人。
请严格输出符合给定 JSON Schema 的编曲数据，用于导出 MIDI 并在 FL Studio 二次编辑。

硬性要求：
- 只输出 JSON（不要 Markdown、不要解释文字）
- Note 使用绝对 tick：start_tick / duration_tick（从歌曲开始计时）
- blueprint.sections 用 start_bar/end_bar 描述段落，覆盖 total_bars
- tracks 每条必须包含 variants，至少有一个 tag="core"
- 轨道 groove 只用 groove_intent（不要把 groove 写进每个 Note）
- 至少包含：drum、bass、synth_pad/chords、synth_lead/melody 四类轨
""".strip()


class MusicAgent:
    def __init__(self):
        print("初始化 Music Agent！")
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL"),
        )
        self.model = os.getenv("MODEL_NAME")

        if not self.model:
            raise RuntimeError("MODEL_NAME 未设置")
        if not os.getenv("API_KEY"):
            raise RuntimeError("API_KEY 未设置")
        # BASE_URL 可以为空，表示默认官方地址

    def generate_music(self, prompt: str) -> ArrangementSchema:
        """
        发送 Prompt -> 获取严格 JSON -> Pydantic 校验 -> 返回对象
        """
        schema_wrapper = build_json_schema_for_openai()

        print(f"🎵 正在思考中: {prompt}")
        start_time = time.time()

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={
                "type": "json_schema",
                "json_schema": schema_wrapper,
            },
        )

        raw_json = resp.choices[0].message.content or ""
        print(f"✅ AI 响应耗时: {time.time() - start_time:.2f}s")

        # 严格模式下基本就是干净 JSON；这里仍做极轻量清洗防护
        cleaned = raw_json.strip().replace("```json", "").replace("```", "").strip()

        try:
            arrangement = ArrangementSchema.model_validate_json(cleaned)  # Pydantic v2 推荐写法 :contentReference[oaicite:3]{index=3}
            return arrangement
        except ValidationError as e:
            print("❌ Pydantic 校验失败（说明字段缺失/类型不匹配/枚举错误）")
            print(e)
            print("\n=== 原始输出（截断） ===")
            print(cleaned[:2000])
            raise


if __name__ == "__main__":
    agent = MusicAgent()

    user_request = """
请创作一首 124 BPM 的 Deep House，C Minor，16 bars。

段落：
- Intro: 1-4 bars（pad + hats，能量低）
- Build-up: 5-8 bars（加 perc / 提升密度）
- Drop: 9-16 bars（加入 kick + bass + lead，能量最高）

和弦进行（每2小节一个和弦）：
- Cm | Cm | Fm | Fm | Ab | Ab | Gm | Gm

要求：
- drum：kick 4 on the floor；hats 1/8 或 1/16，intro 轻、drop 更密
- bass：跟随和弦根音，避让 kick（kick 处可缩短或错位）
- pad/chords：长音铺底
- lead：C minor 音阶内，drop 有 hook
""".strip()

    try:
        # 1) 生成结构化对象
        result = agent.generate_music(user_request)

        # 2) debug：落盘 JSON（方便你检查 ticks/段落/notes）
        Path("../last_arrangement.json").write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8"
        )
        print("🧾 saved: last_arrangement.json")

        # 3) 调 service -> renderer 导出 MIDI
        out_mid = export_midi(result, out_path="../output.mid", ppq=480, seed=2026)
        print(f"\n🚀 完成！请用 FL Studio 打开: {out_mid}")

    except Exception as e:
        print(f"💥 程序出错: {e}")
        print(traceback.format_exc())
