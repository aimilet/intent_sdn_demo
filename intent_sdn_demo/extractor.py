"""远程模型抽取模块：仅将自然语言转为受 Schema 约束的候选 Intent IR。"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import IncompleteRead
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import ActorRole
from intent_sdn_demo.validation import reject_unknown_fields


LOGGER = logging.getLogger(__name__)
MAX_LLM_CONFIG_BYTES = 16 * 1024
MAX_LLM_RESPONSE_BYTES = 1024 * 1024
OLLAMA_NUM_PREDICT_LIMIT = 4096
SUPPORTED_LLM_PROVIDERS = frozenset({"openai", "ollama"})


class IntentExtractor(Protocol):
    """文本意图抽取的可替换接口，便于以假实现覆盖自动化测试。"""

    def extract(self, text: str, actor_role: ActorRole) -> Mapping[str, object]:
        """返回仅含 intents 数组的 JSON 对象。"""


@dataclass(frozen=True)
class LlmConfig:
    """远程聊天模型接口配置，不将密钥写入日志或响应。"""

    base_url: str
    api_key: str
    model: str
    provider: str = "openai"
    timeout_seconds: float = 30.0
    # 配置来源仅由加载器内部标记，禁止调用方伪造内容写入日志。
    config_source: str = field(default="direct", init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        """校验所有配置来源，防止非法地址、超长字段或非有限超时进入网络请求。"""

        base_url = self.base_url.strip() if isinstance(self.base_url, str) else ""
        api_key = self.api_key.strip() if isinstance(self.api_key, str) else ""
        model = self.model.strip() if isinstance(self.model, str) else ""
        provider = self.provider.strip() if isinstance(self.provider, str) else ""
        if provider not in SUPPORTED_LLM_PROVIDERS:
            raise IntentError(
                "invalid_llm_config",
                "LLM provider 只支持 openai 或 ollama。",
                503,
            )
        if not base_url or len(base_url) > 2048:
            raise IntentError(
                "invalid_llm_config",
                "LLM base_url 必须是长度受限的非空地址。",
                503,
            )
        if not _is_valid_base_url(base_url):
            raise IntentError("invalid_llm_config", "LLM base_url 必须是合法的 HTTP(S) 地址。", 503)
        if not api_key or len(api_key) > 4096 or _contains_control_character(api_key):
            raise IntentError(
                "invalid_llm_config",
                "LLM api_key 必须是长度受限且不含控制字符的非空字符串。",
                503,
            )
        if not model or len(model) > 200 or _contains_control_character(model):
            raise IntentError(
                "invalid_llm_config",
                "LLM model 必须是长度受限且不含控制字符的非空字符串。",
                503,
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 300
        ):
            raise IntentError(
                "invalid_llm_config",
                "LLM timeout_seconds 必须是大于 0 且不超过 300 的有限数值。",
                503,
            )
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

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
        config = cls(base_url=base_url, api_key=api_key, model=model, provider="openai")
        object.__setattr__(config, "config_source", "environment")
        return config

    @classmethod
    def from_json_file(cls, file_path: str | os.PathLike[str]) -> "LlmConfig":
        """读取严格 JSON 配置；已设置的环境变量优先覆盖文件中的同名连接字段。"""

        path = Path(file_path)
        try:
            mode = path.stat().st_mode
            with path.open("rb") as config_file:
                raw_bytes = config_file.read(MAX_LLM_CONFIG_BYTES + 1)
            if len(raw_bytes) > MAX_LLM_CONFIG_BYTES:
                raise IntentError(
                    "invalid_llm_config",
                    "LLM JSON 配置文件超过 16 KiB 限制。",
                    503,
                )
            raw_config = raw_bytes.decode("utf-8")
        except IntentError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise IntentError(
                "invalid_llm_config",
                "LLM JSON 配置文件不可读取或不是合法 UTF-8。",
                503,
            ) from exc
        try:
            data = json.loads(raw_config, object_pairs_hook=_unique_config_object)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IntentError("invalid_llm_config", "LLM JSON 配置文件不是合法 JSON。", 503) from exc
        if not isinstance(data, Mapping):
            raise IntentError("invalid_llm_config", "LLM JSON 配置必须是对象。", 503)
        unknown_fields = sorted(
            str(key)
            for key in data
            if not isinstance(key, str)
            or key not in {"provider", "base_url", "api_key", "model", "timeout_seconds"}
        )
        if unknown_fields:
            raise IntentError(
                "invalid_llm_config",
                f"LLM JSON 配置含不支持字段：{', '.join(unknown_fields)}。",
                503,
            )
        env_base_url = os.environ.get("LLM_BASE_URL", "").strip()
        env_api_key = os.environ.get("LLM_API_KEY", "").strip()
        env_model = os.environ.get("LLM_MODEL", "").strip()
        base_url = env_base_url or data.get("base_url")
        api_key = env_api_key or data.get("api_key")
        model = env_model or data.get("model")
        config = cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            provider=data.get("provider", "openai"),
            timeout_seconds=data.get("timeout_seconds", 30.0),
        )
        overridden = tuple(
            name
            for name, value in (
                ("base_url", env_base_url),
                ("api_key", env_api_key),
                ("model", env_model),
            )
            if value
        )
        source = "json" if not overridden else f"json+env:{','.join(overridden)}"
        object.__setattr__(config, "config_source", source)
        if os.name == "posix" and mode & 0o077:
            # WSL 的 drvfs 可能不支持 chmod，因此只告警并建议迁移，不伪装权限已收紧。
            LOGGER.warning(
                "LLM JSON 配置文件的组/其他用户权限过宽；"
                "请移至支持 POSIX 权限的目录并设置为 600。"
            )
        if overridden:
            LOGGER.warning(
                "LLM JSON 配置被环境变量覆盖：字段=%s；实际请求使用覆盖后的值。",
                ",".join(overridden),
            )
        return config

    @property
    def endpoint(self) -> str:
        """按提供方兼容服务根地址、API 根地址或完整聊天端点。"""

        base_url = self.base_url.rstrip("/")
        if self.provider == "ollama":
            if base_url.endswith("/api/chat"):
                return base_url
            if base_url.endswith("/api"):
                return f"{base_url}/chat"
            return f"{base_url}/api/chat"
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"


def _unique_config_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """构造配置对象并拒绝重复键，避免同一字段出现歧义值。"""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复配置字段：{key}")
        result[key] = value
    return result


def _is_valid_base_url(value: str) -> bool:
    """验证远程地址结构和端口，禁止 URL 内嵌凭证、查询参数或片段。"""

    parsed = urlparse(value)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and hostname
        and not any(character.isspace() for character in value)
        and not _contains_control_character(value)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _contains_control_character(value: str) -> bool:
    """拒绝换行等 ASCII 控制字符，防止 HTTP 头和日志注入。"""

    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class RemoteIntentExtractor:
    """使用 OpenAI 兼容或 Ollama 原生接口抽取 JSON，并交给本地规则校验。"""

    def __init__(self, config: LlmConfig | None = None) -> None:
        self._config = config
        if config is not None:
            _log_config(config)

    def extract(self, text: str, actor_role: ActorRole) -> Mapping[str, object]:
        """调用远程模型并只接受包含 intents 的合法 JSON 对象。"""

        config = self._config or LlmConfig.from_environment()
        messages = _messages(text, actor_role)
        body = _request_body(config, messages)
        request = Request(
            config.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        if self._config is None:
            _log_config(config)
        LOGGER.info(
            "提交远程意图抽取请求：提供方=%s，角色=%s，文本长度=%s",
            config.provider,
            actor_role.value,
            len(text),
        )
        try:
            response = urlopen(request, timeout=config.timeout_seconds)
        except HTTPError as exc:
            LOGGER.warning(
                "远程意图抽取服务返回 HTTP 状态：提供方=%s，状态=%s",
                config.provider,
                exc.code,
            )
            if exc.code in {401, 403}:
                raise IntentError(
                    "llm_auth_failed",
                    "远程模型认证或模型访问权限被拒绝，请检查 API Key 和模型权限。",
                    503,
                ) from exc
            if exc.code == 404:
                raise IntentError(
                    "llm_endpoint_not_found",
                    "远程模型接口或模型不存在，请检查 provider、base_url 和 model。",
                    503,
                ) from exc
            if exc.code == 429:
                raise IntentError(
                    "llm_rate_limited",
                    "远程模型服务已限流，请稍后重试。",
                    503,
                ) from exc
            raise IntentError("llm_request_failed", "远程意图抽取服务拒绝了请求。", 503) from exc
        except (TimeoutError, URLError, OSError) as exc:
            # urlopen 同时覆盖建连和等待响应头，不伪造更细的阶段判断。
            _raise_transport_error(config, exc, "open")

        try:
            with response:
                raw_bytes = response.read(MAX_LLM_RESPONSE_BYTES + 1)
                if len(raw_bytes) > MAX_LLM_RESPONSE_BYTES:
                    raise IntentError(
                        "invalid_llm_output",
                        "远程模型响应超过 1 MiB 限制。",
                        422,
                    )
                raw_response = raw_bytes.decode("utf-8")
        except IntentError:
            raise
        except UnicodeDecodeError as exc:
            LOGGER.warning("远程意图抽取服务返回了非 UTF-8 响应。")
            raise IntentError("invalid_llm_output", "远程模型未返回合法的意图 JSON。", 422) from exc
        except (IncompleteRead, TimeoutError, URLError, OSError) as exc:
            _raise_transport_error(config, exc, "read")

        try:
            response_data = json.loads(raw_response)
            content = _response_content(config, response_data)
            extracted = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise IntentError("invalid_llm_output", "远程模型未返回合法的意图 JSON。", 422) from exc
        if not isinstance(extracted, Mapping):
            raise IntentError("invalid_llm_output", "远程模型返回的意图 JSON 必须是对象。", 422)
        reject_unknown_fields(extracted, frozenset({"intents"}), "模型输出")
        return extracted


def _messages(text: str, actor_role: ActorRole) -> list[dict[str, str]]:
    """构造两种协议共用的受限系统指令和用户原文消息。"""

    return [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": (
                f"提交者角色固定为 {actor_role.value}。仅解析下列原文，"
                "不要输出角色、命令、路径、端口、队列编号或未出现的数值。\n"
                f"原文：{text}"
            ),
        },
    ]


def _request_body(
    config: LlmConfig, messages: list[dict[str, str]]
) -> dict[str, object]:
    """按提供方生成请求体；Ollama Cloud 不声明其不支持的 structured outputs。"""

    if config.provider == "ollama":
        return {
            "model": config.model,
            "messages": messages,
            "stream": False,
            # Cloud thinking 模型默认可能长时间思考；关闭思考并限制输出避免代理读超时。
            "think": False,
            "options": {"temperature": 0, "num_predict": OLLAMA_NUM_PREDICT_LIMIT},
        }
    return {
        "model": config.model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }


def _response_content(config: LlmConfig, response_data: object) -> str:
    """从对应协议的非流式响应中读取文本，拒绝缺失或非字符串内容。"""

    if not isinstance(response_data, Mapping):
        raise ValueError("远程响应必须是对象。")
    if config.provider == "ollama":
        content = response_data["message"]["content"]
    else:
        content = response_data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content:
        raise ValueError("远程响应 content 必须是非空字符串。")
    return content


def _log_config(config: LlmConfig) -> None:
    """记录不含密钥的连接摘要，帮助区分 JSON 与环境变量实际生效配置。"""

    LOGGER.info(
        "远程模型配置：提供方=%s，主机=%s，端点=%s，模型=%s，超时=%ss，来源=%s",
        config.provider,
        _endpoint_host(config.endpoint),
        urlparse(config.endpoint).path,
        config.model,
        config.timeout_seconds,
        config.config_source,
    )


def _endpoint_host(endpoint: str) -> str:
    """提取已校验端点的主机名，日志不记录完整 URL 或认证信息。"""

    return urlparse(endpoint).hostname or "unknown"


def _raise_transport_error(config: LlmConfig, error: BaseException, phase: str) -> None:
    """按网络阶段和 URLError.reason 分类记录，并转换为安全的领域错误。"""

    kind = _classify_transport_error(error, phase)
    LOGGER.warning(
        "远程意图抽取网络失败：提供方=%s，主机=%s，阶段=%s，类型=%s",
        config.provider,
        _endpoint_host(config.endpoint),
        phase,
        kind,
    )
    if kind == "timeout":
        raise IntentError("llm_timeout", "远程模型请求超时，请稍后重试。", 503) from error
    raise IntentError("llm_unavailable", "远程意图抽取服务暂时不可用。", 503) from error


def _classify_transport_error(error: BaseException, phase: str) -> str:
    """仅依据异常类型安全区分 timeout、DNS、connect、TLS 和 read。"""

    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, ssl.SSLError):
        return "tls"
    if phase == "read":
        return "read"
    if isinstance(reason, (ConnectionError, OSError)):
        return "connect"
    return "network"


def _system_prompt() -> str:
    """返回不可包含执行细节的固定抽取指令，降低模型越权概率。"""

    return """你是车联网通信意图抽取器。只返回 JSON 对象，格式为：
{"intents":[{"scope":{"vehicle_ids":[...],"traffic_class":"emergency|control|navigation|video|all"},"objective":"prioritize_traffic|minimize_latency|relieve_network_congestion|limit_background_traffic","service":"emergency_v2x|vehicle_control|navigation|background_video","strength":"must|prefer","priority":"critical|high|normal|low","constraints":[{"metric":"latency_ms|min_bandwidth_mbps|max_bandwidth_mbps","operator":"<=|>=","value":数字,"unit":"ms|Mbps"}],"semantic_requirements":[{"metric":"latency|bandwidth|reliability","level":"low|medium|high","origin":"explicit|inferred","evidence":"原文片段"}],"evidence":["原文片段"],"ambiguities":["无法确定的内容"]}]}
规则：service 必须与 traffic_class 的固定业务映射一致；只使用原文明确出现的数值约束；semantic_requirements 只表达语义等级，不填写数值；不确定时将原因放入 ambiguities；不支持道路交通流控制；不得输出 Markdown、解释、命令、路径、端口、队列编号或任何其他字段。"""
