"""应用服务层：为 Web API 连接解析、仲裁和策略编译，隔离 HTTP 细节。"""

from __future__ import annotations

from collections.abc import Mapping

from intent_sdn_demo.arbitration import IntentArbitrator
from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.extractor import IntentExtractor
from intent_sdn_demo.models import DecisionBundle, IntentEnvelope
from intent_sdn_demo.parser import IntentParser
from intent_sdn_demo.policy import PolicyCompiler
from intent_sdn_demo.topology import TopologyInventory, default_topology
from intent_sdn_demo.validation import envelope_from_dict, parse_actor_role, parse_source_channel


class IntentSdnService:
    """新版 Demo 的同步应用入口，当前不执行 Mininet 下发动作。"""

    def __init__(
        self,
        *,
        topology: TopologyInventory | None = None,
        extractor: IntentExtractor | None = None,
    ) -> None:
        self.topology = topology or default_topology()
        self._parser = IntentParser(self.topology, extractor)
        self._arbitrator = IntentArbitrator()
        self._compiler = PolicyCompiler(self.topology)

    def parse_request(self, payload: object) -> IntentEnvelope:
        """处理 parse API 的外部输入，确保通道和角色均为受限枚举。"""

        data = _expect_mapping(payload, "parse 请求")
        source_channel = parse_source_channel(data.get("source_channel"))
        actor_role = parse_actor_role(data.get("actor_role"))
        return self._parser.parse(
            source_channel=source_channel,
            actor_role=actor_role,
            payload=data.get("payload"),
        )

    def compile_request(self, payload: object) -> DecisionBundle:
        """二次校验完整 IR 后生成可预览但尚未下发的决策包。"""

        data = _expect_mapping(payload, "compile 请求")
        envelopes_payload = data.get("envelopes")
        if envelopes_payload is None:
            envelopes_payload = [data.get("envelope")]
        if not isinstance(envelopes_payload, list) or not envelopes_payload:
            raise IntentError("invalid_envelopes", "compile 请求需要 envelope 或非空 envelopes 数组。")
        if len(envelopes_payload) > 10:
            raise IntentError("too_many_envelopes", "单次汇总最多包含 10 个意图请求。")
        envelopes = tuple(
            envelope_from_dict(item, self.topology) for item in envelopes_payload
        )
        arbitration = self._arbitrator.resolve(envelopes)
        return self._compiler.decide(arbitration)

    def topology_snapshot(self) -> dict[str, object]:
        """返回前端可安全展示的固定拓扑摘要。"""

        return self.topology.to_dict()


def _expect_mapping(payload: object, name: str) -> Mapping[str, object]:
    """确保 HTTP 解码后的请求是对象而非数组或标量。"""

    if not isinstance(payload, Mapping):
        raise IntentError("invalid_request", f"{name}必须是 JSON 对象。")
    return payload
