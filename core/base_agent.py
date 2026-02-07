import os
import json
import re
import time
from typing import Type,TypeVar,Optional,Any
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel,ValidationError

# 加载环境变量
load_dotenv()

T=TypeVar("T",bound=BaseModel)

class BaseAgent:
    def __init__(self,system_prompt:str=""):
        self.client=OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL")
        )
        self.model=os.getenv("MODEL_NAME")
        if not self.model:
            raise ValueError("模型名称不可用")
        self.system_prompt=system_prompt
    def _clean_json_text(self,text:str) -> str:
        """
            数据清洗。
            即使模型输出了 ```json ... ``` 或其他杂质，也能提取出纯 JSON。
        """
        if not text:
            return ""
        # 1.移除markdown代码块包裹
        text=text.strip()
        if "```" in text:
            pattern = r"```(?:json)?\s*(.*?)\s*```"
            match = re.search(pattern, text, re.DOTALL)
            if match:
                text = match.group(1)
        extracted = self._extract_first_json_object(text)
        return extracted or text

    def _extract_first_json_object(self, text: str) -> str:
        """
        Extract the first top-level JSON object from text.
        Handles extra trailing text and ignores braces inside strings.
        """
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
                elif ch == '\"':
                    in_str = False
                continue
            if ch == '\"':
                in_str = True
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return ""

    def call_llm(
            self,
            user_prompt:str,
            response_model:Optional[Type[T]]=None,
            temperature:float=0.2,
            max_tokens:int=2000,
            retries:int=2
        ) -> Any:
        """
        统一的LLM调用接口。

        Args:
            user_prompt:用户的指令
            response_model:（可选) Pydantic 模型类。如果不传，返回原始 JSON 字典。
            temperature:随机性
            max_tokens:最大token
            retries:重试次数
        """

        messages=[
            {"role":"system","content":self.system_prompt},
            {"role":"user","content":user_prompt}
        ]
        last_exception: Exception | None = None
        content = ""
        # 解析JSON+模型验证
        for r in range(retries + 1):
            try:
                # 1.调用OpenAI（强制Json Model）
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = resp.choices[0].message.content
                if not content:
                    raise ValueError("Empty response from LLM")
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    cleaned = self._clean_json_text(content)
                    if not cleaned:
                        raise
                    data = json.loads(cleaned)
                if response_model:
                    return response_model.model_validate(data)
                return data
            # 捕获所有异常，进行提示修正
            except Exception as e:
                last_exception = e
                # 轻量 backoff
                time.sleep(0.3 * (r + 1))
                if r < retries:
                    # ✅ 只在 content 非空时回喂 assistant 内容
                    if content:
                        messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"Your JSON output is invalid or fails schema validation: {e}. "
                                   f"Please output ONLY a valid JSON object that matches the schema."
                    })
        raise last_exception or RuntimeError("LLM call failed")
