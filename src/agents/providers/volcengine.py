"""
VolcEngine (豆包) provider implementation using Ark runtime SDK.
"""

import os
import json
import logging
from typing import Optional, Type, List, Any

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .base import LLMProvider, LLMResponse, RateLimitError, is_retryable

try:
    from volcenginesdkarkruntime import Ark  # type: ignore
    from volcenginesdkarkruntime._exceptions import (
        ArkAPIStatusError,  # type: ignore
        ArkAuthenticationError,  # type: ignore
        ArkRateLimitError,  # type: ignore
    )
    from volcenginesdkarkruntime.types.chat import ChatCompletion as ArkChatCompletion  # type: ignore
    _VOLC_AVAILABLE = True
except ImportError:
    # Handle case where SDK is not installed
    # Define dummy classes to satisfy type checkers (Zero Redlines rule)
    _VOLC_AVAILABLE = False
    
    class Ark:  # type: ignore
        def __init__(self, base_url=None, api_key=None, region=None): pass
        chat: Any = None
        
    class ArkAPIStatusError(Exception):  # type: ignore
        status_code: int = 0
        
    class ArkAuthenticationError(Exception): pass  # type: ignore
    class ArkRateLimitError(Exception): pass  # type: ignore
    
    class ArkChatCompletion:  # type: ignore
        choices: List[Any] = []
        usage: Any = None

logger = logging.getLogger(__name__)


class VolcEngineProvider(LLMProvider):
    """
    火山引擎方舟（豆包）Provider，使用 volcenginesdkarkruntime SDK。

    通过 Ark 客户端调用豆包大模型进行文本生成，支持 JSON Mode 结构化输出。
    认证方式: API Key，从环境变量 VOLC_API_KEY 读取。
    """

    def __init__(
        self,
        region: Optional[str] = None,
    ) -> None:
        # --- 从环境变量加载配置 ---
        self.base_url = os.environ.get("VOLC_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"

        # VOLC_MODEL_NAME: 逻辑模型名称 (e.g. "doubao-pro-32k")
        # 用于 Token 计费匹配 (token_tracker.py) 和日志记录
        # 如果未指定，默认使用 endpoint_id，但这可能导致计费匹配失败
        self.model_id = os.environ.get("VOLC_MODEL_ID") or ""

        # VOLC_REGION: 服务区域，默认北京（cn-beijing）
        self.region = region or os.environ.get("VOLC_REGION") or "cn-beijing"

        # VOLC_API_KEY: 火山方舟 API Key (替代 AK/SK)
        self.api_key = os.environ.get("VOLC_API_KEY")

        # VOLC_PROJECT_NAME: 项目名称，默认为 "default"
        # 某些 API 调用需要指定项目上下文
        self.project_name = os.environ.get("VOLC_PROJECT_NAME", "default")

        # --- 校验必填项 ---
        if not self.api_key:
            raise ValueError(
                "VolcEngine Auth is required. "
                "Provide VOLC_API_KEY in .env."
            )

        # --- 初始化 Ark 客户端 ---
        # volcenginesdkarkruntime.Ark 是方舟推理的官方客户端
        # 支持 API Key 鉴权
        if not _VOLC_AVAILABLE:
            raise ImportError("volcenginesdkarkruntime is not installed")

        self.client = Ark(
            base_url=self.base_url,
            api_key=self.api_key,
            region=self.region,
        )

    @property
    def name(self) -> str:
        return "volcengine"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception(is_retryable),
    )
    def generate(self, prompt: str, schema: Type[BaseModel]) -> LLMResponse:
        """
        调用豆包模型生成结构化 JSON 输出。

        流程:
        1. 将 Pydantic Schema 转为 JSON，写入 system message 指导模型输出格式
        2. 调用 chat.completions.create()，开启 JSON Mode
        3. 从返回值中提取文本和 token 用量
        """
        # --- 构建 system message，包含 JSON Schema 指令 ---
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        system_msg = (
            "You are a data extraction assistant. "
            "Return ONLY valid JSON matching this schema:\n"
            f"{schema_json}"
        )

        try:
            # --- 调用豆包模型 ---
            # model: 使用 model_id 指定要调用的推理终端
            # response_format: 设置为 json_object 启用 JSON Mode
            # stream=False: 使用同步模式（非流式），确保返回完整的 ChatCompletion
            # extra_headers: 传递项目名称 (X-Project-Name) 用于鉴权
            raw_response = self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                extra_headers={"X-Project-Name": self.project_name},
                stream=False,
            )

            # --- 类型收窄: create() 返回联合类型，stream=False 时实际为 ChatCompletion ---

            if not isinstance(raw_response, ArkChatCompletion):
                raise TypeError(
                    f"Unexpected response type: {type(raw_response).__name__}"
                )
            response = raw_response
        except ArkRateLimitError as e:
            # 429 限流错误：请求过于频繁，需要等待后重试
            raise RateLimitError(self.name, 429, str(e)) from e
        except ArkAuthenticationError as e:
            # 鉴权失败：AK/SK 无效或过期，无法重试，直接抛出
            raise ValueError(
                f"VolcEngine 鉴权失败，请检查 VOLC_API_KEY: {e}"
            ) from e
        except ArkAPIStatusError as e:
            # 其他 HTTP 错误（如 503 服务不可用、余额不足等）
            if e.status_code in (429, 503):
                raise RateLimitError(self.name, e.status_code, str(e)) from e
            raise

        # --- 提取响应文本 ---
        choice = response.choices[0] if response.choices else None
        response_text = ""
        if choice and choice.message and choice.message.content:
            response_text = choice.message.content

        # --- 提取 token 用量，用于成本审计 ---
        prompt_tokens = 0
        completion_tokens = 0
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0

        return LLMResponse(
            text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=self.model_id,
        )
