"""远程模型抽取模块：仅将自然语言转为受 Schema 约束的候选 Intent IR。"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import ActorRole
from intent_sdn_demo.validation import reject_unknown_fields


LOGGER = logging.getLogger(__name__)


class IntentExtractor(Protocol):
    """文本意图抽取的可替换接口，便于以假实现覆盖自动化测试。"""

    def extract(self, text: str, actor_role: ActorRole) -> Mapping[str, object]:
        """返回仅含 intents 数组的 JSON 对象。"""


@dataclass(frozen=True)
class LlmConfig:
    """远程 OpenAI 兼容接口所需配置，不将密钥写入日志或响应。"""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "LlmConfig":
        """从环境变量读取配置，任一缺失时明确拒绝文字解析请求。"""

        base_url = os.environ.get("LLM_BASE_URL", "").strip()
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        if not base_url or not api_key or not model:
            raise IntentError(
                "llm_not_configured",
                "文字和语音意图解析未配置远程模型，请设置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL。",
                503,
            )
        return cls(base_url=base_url, api_key=api_key, model=model)

    @property
    def endpoint(self) -> str:
        """兼容传入服务根地址或完整 chat completions 地址的配置方式。"""

        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/v1/chat/completions"


class RemoteIntentExtractor:
    """使用 OpenAI 兼容接口进行 JSON 抽取，后续仍由本地规则完整校验。"""

    def __init__(self, config: LlmConfig | None = None) -> None:
        self._config = config

    def extract(self, text: str, actor_role: ActorRole) -> Mapping[str, object]:
        """调用远程模型并只接受包含 intents 的合法 JSON 对象。"""

        config = self._config or LlmConfig.from_environment()
        body = {
            "model": config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"提交者角色固定为 {actor_role.value}。仅解析下列原文，"
                        "不要输出角色、命令、路径、端口、队列编号或未出现的数值。\n"
                        f"原文：{text}"
                    ),
                },
            ],
        }
        request = Request(
            config.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        LOGGER.info("提交远程意图抽取请求：角色=%s，文本长度=%s", actor_role.value, len(text))
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw_response = response.read().decode("utf-8")
        except HTTPError as exc:
            LOGGER.warning("远程意图抽取服务返回 HTTP 状态：%s", exc.code)
            raise IntentError("llm_request_failed", "远程意图抽取服务拒绝了请求。", 503) from exc
        except UnicodeDecodeError as exc:
            LOGGER.warning("远程意图抽取服务返回了非 UTF-8 响应。")
            raise IntentError("invalid_llm_output", "远程模型未返回合法的意图 JSON。", 422) from exc
        except (TimeoutError, URLError, OSError) as exc:
            LOGGER.warning("远程意图抽取服务不可用：%s", type(exc).__name__)
            raise IntentError("llm_unavailable", "远程意图抽取服务暂时不可用。", 503) from exc

        try:
            response_data = json.loads(raw_response)
            content = response_data["choices"][0]["message"]["content"]
            extracted = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise IntentError("invalid_llm_output", "远程模型未返回合法的意图 JSON。", 422) from exc
        if not isinstance(extracted, Mapping):
            raise IntentError("invalid_llm_output", "远程模型返回的意图 JSON 必须是对象。", 422)
        reject_unknown_fields(extracted, frozenset({"intents"}), "模型输出")
        return extracted


def _system_prompt() -> str:
    """返回不可包含执行细节的固定抽取指令，降低模型越权概率。"""

    return """你是车联网通信意图抽取器。只返回 JSON 对象，格式为：
{"intents":[{"scope":{"vehicle_ids":[...],"traffic_class":"emergency|control|navigation|video|all"},"objective":"prioritize_traffic|minimize_latency|relieve_network_congestion|limit_background_traffic","service":"emergency_v2x|vehicle_control|navigation|background_video","strength":"must|prefer","priority":"critical|high|normal|low","constraints":[{"metric":"latency_ms|min_bandwidth_mbps|max_bandwidth_mbps","operator":"<=|>=","value":数字,"unit":"ms|Mbps"}],"semantic_requirements":[{"metric":"latency|bandwidth|reliability","level":"low|medium|high","origin":"explicit|inferred","evidence":"原文片段"}],"evidence":["原文片段"],"ambiguities":["无法确定的内容"]}]}
规则：service 必须与 traffic_class 的固定业务映射一致；只使用原文明确出现的数值约束；semantic_requirements 只表达语义等级，不填写数值；不确定时将原因放入 ambiguities；不支持道路交通流控制；不得输出 Markdown、解释、命令、路径、端口、队列编号或任何其他字段。"""
