"""输入适配模块：统一处理文字、语音转写和 JSON，输出经过校验的 IntentEnvelope。"""

from __future__ import annotations

import json
from collections.abc import Mapping

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.extractor import IntentExtractor, RemoteIntentExtractor
from intent_sdn_demo.models import ActorRole, IntentEnvelope, SourceChannel
from intent_sdn_demo.topology import TopologyInventory
from intent_sdn_demo.validation import build_envelope, reject_unknown_fields


class IntentParser:
    """路由不同输入类型，并确保 JSON 与模型输出经过同一校验边界。"""

    def __init__(
        self, topology: TopologyInventory, extractor: IntentExtractor | None = None
    ) -> None:
        self._topology = topology
        self._extractor = extractor or RemoteIntentExtractor()

    def parse(
        self, *, source_channel: SourceChannel, actor_role: ActorRole, payload: object
    ) -> IntentEnvelope:
        """将 API 请求载荷转换为统一 Intent IR。"""

        if source_channel is SourceChannel.JSON:
            return self._parse_json(actor_role, payload)
        return self._parse_text(source_channel, actor_role, payload)

    def _parse_json(self, actor_role: ActorRole, payload: object) -> IntentEnvelope:
        """结构化输入不经过模型，直接读取 intents 数组。"""

        data = self._coerce_json_object(payload)
        reject_unknown_fields(data, frozenset({"intents"}), "结构化输入")
        intents = data.get("intents")
        return build_envelope(
            source_channel=SourceChannel.JSON,
            actor_role=actor_role,
            original_text="结构化 JSON 输入",
            intents_payload=intents,
            topology=self._topology,
        )

    def _parse_text(
        self, source_channel: SourceChannel, actor_role: ActorRole, payload: object
    ) -> IntentEnvelope:
        """文字和语音转写共用远程模型抽取链路，不提供关键词降级逻辑。"""

        if not isinstance(payload, str) or not payload.strip():
            raise IntentError("invalid_text", "文字或语音转写内容必须是非空字符串。")
        text = payload.strip()
        if len(text) > 2000:
            raise IntentError("text_too_long", "文字或语音转写内容不能超过 2000 个字符。")
        extracted = self._extractor.extract(text, actor_role)
        if not isinstance(extracted, Mapping):
            raise IntentError("invalid_llm_output", "远程模型返回的意图 JSON 必须是对象。", 422)
        reject_unknown_fields(extracted, frozenset({"intents"}), "模型输出")
        intents = extracted.get("intents")
        return build_envelope(
            source_channel=source_channel,
            actor_role=actor_role,
            original_text=text,
            intents_payload=intents,
            topology=self._topology,
        )

    def _coerce_json_object(self, payload: object) -> Mapping[str, object]:
        """接受 HTTP 已解码对象或 JSON 字符串，拒绝其他类型。"""

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise IntentError("invalid_json", "结构化输入不是合法 JSON。") from exc
        if not isinstance(payload, Mapping):
            raise IntentError("invalid_json", "结构化输入必须是 JSON 对象。")
        return payload
