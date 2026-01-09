import os
from http.client import responses

from openai import OpenAI, base_url
from dotenv import load_dotenv

load_dotenv()

client=OpenAI(
    api_key=os.getenv("API_KEY"),
    base_url=os.getenv("BASE_URL")
)
response =client.chat.completions.create(
    model=os.getenv("MODEL_NAME"),
    messages=[
        {"role":"system","content":"你是一个乐理助手。"},
        {"role":"user","content":"用一句话解释什么是 Phrygian 调式？"}
    ]
)
print(response.choices[0].message.content)