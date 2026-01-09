import os
import json
import time
from dotenv import load_dotenv
from openai import OpenAI
from pydantic.v1.schema import schema

from schema import ArrangementSchema
from service import generate_midi_file
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
        你是一个专业的 AI 音乐作曲家。
        你的任务是根据用户的描述，通过生成 MIDI 数据来创作音乐。

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
            data_dict=json.loads(cleaned_json)
            arrangement=ArrangementSchema(**data_dict)
            return arrangement
        except json.JSONDecodeError:
            print("❌ JSON 解析失败！AI 返回的不是合法 JSON。")
            print(cleaned_json)
            raise
        except Exception as e:
            print(f"❌ Pydantic 验证失败：{e}")
            raise


# --- 测试代码 ---
if __name__ == "__main__":
    agent = MusicAgent()

    # 这里的 Prompt 可以随便改
    user_request = "创作一首 140 BPM 的 Cyberpunk 风格战斗音乐，要有强烈的 Bass 和快速的 Lead 旋律。"

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
        print("💥 程序出错，请检查上面的错误信息。")
