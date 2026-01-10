import os
import json
import time
import traceback
import sys
from dotenv import load_dotenv
from openai import OpenAI
from json_repair import repair_json

from schema import ArrangementSchema
from service import generate_midi_file

# 设置输出编码为 UTF-8 (修复 Windows 编码问题)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# 加载环境变量
load_dotenv()

class MusicAgent:
    def __init__(self):
        print("初始化 Music Agent！")
        self.client=OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL")
        )
        self.model=os.getenv("MODEL_NAME")
    def generate_music(self,prompt:str)->ArrangementSchema:
        """
            核心方法：发送 Prompt -> 获取 JSON -> 验证并返回对象
        """
        # 1.获取Schema文本描述
        schema_str=json.dumps(ArrangementSchema.model_json_schema(),ensure_ascii=False)
        # 2.构建System Prompt
        system_content = f"""
        你是一位精通 FL Studio 的顶级电子音乐制作人，拥有深厚的乐理知识。
        你的任务是编写符合音乐理论的 MIDI JSON 数据。

        === 核心规则 ===

        1. 时间单位 (Ticks):
           - 1 拍 = 480 ticks
           - 4/4 拍的一小节 = 1920 ticks
           - 1/8 音符 = 240 ticks
           - 1/16 音符 = 120 ticks

        2. 循环策略 (Looping Strategy):
           - 不要试图一次性写完 32 小节的所有音符，这会产生错误。
           - 请为每个轨道编写一个 "Pattern" (通常是 4 小节或 8 小节)。
           - 在 JSON 中设置 'pattern_length' (例如 4小节=7680) 和 'loop_count' (例如循环 4 次)。
           - Python 代码会自动帮你复制这些音符。

        3. 音乐性要求:
           - Kick (Drum): 必须是稳定的 4/4 拍 (每拍一个)。
           - Bass: 不要只是长音，要有律动 (Groove)，使用切分音。
           - Hi-hats: 这种风格通常需要密集的 1/8 或 1/16 拍。

        4. 和声规则 (Harmonic Rules) - Day 2 核心：
           - 如果用户指定了和弦进行（如 Cm-Ab-Fm-G），必须严格遵守。
           - Bass 音符必须演奏当前和弦的根音（Root Note）。
             例如：Cm 和弦时 Bass 演奏 C (MIDI 36/48)，Ab 和弦时演奏 Ab (MIDI 44/56)。
           - Pad/Piano 必须演奏完整和弦音：
             * 根音（Root）、三度（3rd）、五度（5th）
             * 例如 Cm 和弦 = C(60) + Eb(63) + G(67)
           - 旋律（Lead/Piano）可以使用和弦内音和经过音，但要以和弦内音为主。

        5. 音阶约束 (Scale Constraint):
           - 所有旋律音符必须在指定调式的音阶内。
           - 常见音阶对照：
             * C Minor (自然小调): C(60), D(62), Eb(63), F(65), G(67), Ab(68), Bb(70)
             * C Major: C(60), D(62), E(64), F(65), G(67), A(69), B(71)
             * A Minor: A(57), B(59), C(60), D(62), E(64), F(65), G(67)
           - 如果不确定，优先使用五声音阶（去掉半音）会更安全。

        6. Velocity (力度) 分层 - Day 3 核心：
           - 不要所有音符都用相同力度！这会听起来像机器人。
           - 推荐设置：
             * Kick: 110-120 (强有力)
             * Snare: 正拍 100-110, 副拍/Ghost 50-70
             * Hi-hats: 主拍 80-100, Ghost Notes 30-50 (创造律动感)
             * Bass: 90-105
             * Piano/Lead: 70-100 (根据旋律起伏变化)
             * Pad: 60-80 (柔和的背景)

        IMPORTANT:
        你必须且只能返回纯粹的 JSON 格式数据。
        不要包含任何 Markdown 格式（如 ```json ... ```），只返回 JSON 字符串。
        你的输出必须严格符合以下的 JSON Schema 定义：

        {schema_str}
        """
        print(f"🎵 🎵 🎵 正在思考中:{prompt}")
        start_time=time.time()
        # 3.调用LLM
        response=self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role":"system","content":system_content},
                {"role":"user","content":prompt}
            ],
            # 高级参数，调低温度让 AI 输出更稳定的格式
            temperature=0.1
        )
        raw_content=response.choices[0].message.content
        print(f"✅ AI 响应耗时: {time.time() - start_time:.2f}s")
        # 4. 清洗数据 (防止 AI 有时候还是会加 Markdown 标记)
        cleaned_json = raw_content.replace("```json", "").replace("```", "").strip()
        # 5. Pydantic 验证
        try:
            data_dict = repair_json(cleaned_json, return_objects=True)
            arrangement=ArrangementSchema(**data_dict)
            return arrangement
        except Exception as e:
            print(f"❌ JSON 解析失败: {e}")
            print("\n=== 调试信息：AI 返回的原始 JSON ===")
            print(cleaned_json[:2000])  # 打印前2000字符
            print("=" * 50)
            # 这里可以考虑让 AI 重试
            return None


# --- 测试代码 ---
if __name__ == "__main__":
    agent = MusicAgent()

    # 这里的 Prompt 可以随便改
    user_request = """
        请创作一首 120 BPM 的 Deep House 风格歌曲，调式为 C Minor。

        结构要求 (总共 16 小节，Pattern 8小节循环2次):
        1. Intro (0-8 小节): Synth Pad + Hi-hats
        2. Drop (8-16 小节): 完整编排，加入 Kick、Bass、Lead

        和弦进行 - 每个和弦持续 4 小节:
        - Cm (0-4小节): C根音，和弦 C+Eb+G
        - Fm (4-8小节): F根音，和弦 F+Ab+C

        乐器要求（每个轨道都要有notes数组）：
        1. Drums (drum 类型):
           - Kick (MIDI 36): 每拍一次，velocity 115
           - Hi-hats (MIDI 42): 1/8拍，主拍90，副拍40

        2. Bass (bass 类型):
           - 0-4小节演奏C音(MIDI 48)，4-8小节演奏F音(MIDI 53)
           - 使用切分节奏，velocity 95

        3. Synth Pad (synth_pad 类型):
           - 演奏完整和弦，长音，velocity 70

        4. Lead (synth_lead 类型):
           - C Minor音阶内的旋律，velocity 80-95

        重要：
        - 每个轨道设置 "loop_count": 2, "pattern_length": 7680 (8小节)
        - 所有轨道都必须有 "notes" 数组，即使只有1个音符
        """

    try:
        # 1.思考
        result = agent.generate_music(user_request)

        print("\n🎉 生成成功！结构化数据如下：")
        print(f"🎵 歌名: {result.song_name}")
        print(f"🎼 BPM: {result.bpm}")
        print(f"🎹 调式: {result.root_note} {result.scale}")
        print(f" tracks: {len(result.tracks)} 条轨道")
        # 2.执行生成midi
        filename=f"{result.song_name}.mid"
        generate_midi_file(result,filename)
        print(f"\n🚀大功告成！请用 FL Studio 打开 {filename} 听听看！")
    except Exception as e:
        # 2. 打印具体的错误对象 e
        print(f"💥 程序出错: {e}")
        # 3. 打印详细的堆栈跟踪（这才是最重要的！）
        print("详细错误信息如下：")
        print(traceback.format_exc())

