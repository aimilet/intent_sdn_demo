"""应用服务层：为 Web API 连接解析、仲裁和策略编译，隔离 HTTP 细节。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from threading import Lock, RLock

from intent_sdn_demo.arbitration import IntentArbitrator
from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.execution import MininetExecutor
from intent_sdn_demo.extractor import IntentExtractor
from intent_sdn_demo.grounding import SlaCatalog, default_sla_catalog
from intent_sdn_demo.models import CandidatePlan, DecisionBundle, IntentEnvelope, MetricSnapshot
from intent_sdn_demo.parser import IntentParser
from intent_sdn_demo.policy import PolicyCompiler
from intent_sdn_demo.topology import TopologyInventory, default_topology
from intent_sdn_demo.validation import envelope_from_dict, parse_actor_role, parse_source_channel


LOGGER = logging.getLogger(__name__)


class IntentSdnService:
    """新版 Demo 的同步应用入口，协调预览缓存、受限下发和指标读取。"""

    def __init__(
        self,
        *,
        topology: TopologyInventory | None = None,
        extractor: IntentExtractor | None = None,
        mininet_enabled: bool = False,
        executor: MininetExecutor | None = None,
        sla_catalog: SlaCatalog | None = None,
    ) -> None:
        self.topology = topology or default_topology()
        self._parser = IntentParser(self.topology, extractor)
        self._arbitrator = IntentArbitrator()
        self._compiler = PolicyCompiler(self.topology)
        self._sla_catalog = sla_catalog or default_sla_catalog()
        if executor is not None and mininet_enabled:
            raise ValueError("executor 与 mininet_enabled 不能同时指定。")
        self._executor = (
            executor
            if executor is not None
            else (MininetExecutor() if mininet_enabled else None)
        )
        self._previewed_plans: dict[str, CandidatePlan] = {}
        self._last_metrics: MetricSnapshot | None = None
        self._lock = RLock()
        self._network_lock = Lock()

    def parse_request(self, payload: object) -> IntentEnvelope:
        """处理 parse API 的外部输入，确保通道和角色均为受限枚举。"""

        data = _expect_mapping(payload, "parse 请求")
        _reject_unknown_fields(
            data,
            frozenset({"source_channel", "actor_role", "payload"}),
            "parse 请求",
        )
        source_channel = parse_source_channel(data.get("source_channel"))
        actor_role = parse_actor_role(data.get("actor_role"))
        envelope = self._parser.parse(
            source_channel=source_channel,
            actor_role=actor_role,
            payload=data.get("payload"),
        )
        LOGGER.info(
            "意图解析完成：通道=%s，角色=%s，意图数=%s",
            source_channel.value,
            actor_role.value,
            len(envelope.intents),
        )
        return envelope

    def compile_request(self, payload: object) -> DecisionBundle:
        """二次校验完整 IR 后生成可预览但尚未下发的决策包。"""

        data = _expect_mapping(payload, "compile 请求")
        # 编译、确认和重置共享网络锁，避免新预览与旧执行并发交错。
        with self._network_lock:
            return self._compile_request_locked(data)

    def _compile_request_locked(self, data: Mapping[str, object]) -> DecisionBundle:
        """在网络串行锁内完成校验、Grounding、仲裁与预览缓存。"""

        # 新编译从入口即使旧的确认令牌和指标失效，失败或阻断都不能沿用旧状态。
        with self._lock:
            self._previewed_plans.clear()
            self._last_metrics = None
        _reject_unknown_fields(data, frozenset({"envelope", "envelopes"}), "compile 请求")
        has_envelope = "envelope" in data
        has_envelopes = "envelopes" in data
        if has_envelope == has_envelopes:
            raise IntentError(
                "invalid_envelopes",
                "compile 请求必须且只能提供 envelope 或 envelopes。",
            )
        envelopes_payload = data["envelopes"] if has_envelopes else [data["envelope"]]
        if not isinstance(envelopes_payload, list) or not envelopes_payload:
            raise IntentError("invalid_envelopes", "compile 请求需要 envelope 或非空 envelopes 数组。")
        if len(envelopes_payload) > 10:
            raise IntentError("too_many_envelopes", "单次汇总最多包含 10 个意图请求。")
        envelopes = tuple(
            envelope_from_dict(item, self.topology) for item in envelopes_payload
        )
        # 每次编译均从服务端只读目录重建 Grounding，客户端传回的结果不会被信任。
        grounding_records = self._sla_catalog.ground_envelopes(envelopes)
        grounding_by_intent = {}
        record_index = 0
        for envelope in envelopes:
            for intent in envelope.intents:
                grounding_by_intent[intent] = grounding_records[record_index]
                record_index += 1
        arbitration = self._arbitrator.resolve(envelopes)
        decision = self._compiler.decide(
            arbitration,
            grounding_by_intent,
            grounding_records=grounding_records,
        )
        with self._lock:
            self._previewed_plans.clear()
            self._last_metrics = None
            if decision.selected_plan is not None:
                self._previewed_plans[decision.selected_plan.plan_id] = decision.selected_plan
        LOGGER.info(
            "策略编译完成：请求数=%s，状态=%s，计划=%s",
            len(envelopes),
            decision.status,
            decision.selected_plan.plan_id if decision.selected_plan else "无",
        )
        return decision

    def apply_request(self, payload: object) -> dict[str, object]:
        """仅执行本进程中已经预览过的白名单计划，禁止客户端提交动作或命令。"""

        data = _expect_mapping(payload, "apply 请求")
        _reject_unknown_fields(data, frozenset({"plan_id"}), "apply 请求")
        plan_id = data.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 64:
            raise IntentError("invalid_plan_id", "plan_id 必须是长度受限的非空字符串。")
        with self._network_lock:
            with self._lock:
                plan = self._previewed_plans.get(plan_id)
            if plan is None:
                raise IntentError("plan_not_previewed", "只能确认下发当前服务已预览的策略。", 409)
            if self._executor is None:
                raise IntentError(
                    "mininet_disabled",
                    "当前服务未启用 Mininet 验证，请以 --enable-mininet 并使用 root 权限启动。",
                    409,
                )
            with self._lock:
                self._last_metrics = None
            LOGGER.info("开始执行已确认策略：计划=%s", plan.plan_id)
            try:
                metrics = self._executor.execute(plan)
            except IntentError:
                LOGGER.exception("策略执行失败：计划=%s", plan.plan_id)
                raise
            with self._lock:
                self._last_metrics = metrics
        LOGGER.info("策略执行完成：计划=%s", plan.plan_id)
        return {"status": "applied", "plan_id": plan.plan_id, "metrics": metrics.to_dict()}

    def reset_request(self, payload: object | None = None) -> dict[str, str]:
        """清除本进程策略预览和最近指标；临时 Mininet 拓扑本身已在执行后停止。"""

        if payload is not None:
            data = _expect_mapping(payload, "reset 请求")
            _reject_unknown_fields(data, frozenset(), "reset 请求")
        with self._network_lock:
            with self._lock:
                self._previewed_plans.clear()
                self._last_metrics = None
            if self._executor is not None:
                result = self._executor.reset()
                LOGGER.info("已重置 Mininet 策略状态。")
                return result
        LOGGER.info("已重置本地策略预览和指标缓存。")
        return {"status": "reset", "message": "已清除本地策略预览和指标缓存。"}

    def metrics_snapshot(self) -> dict[str, object]:
        """返回最近一次已确认下发后的真实指标，未执行时明确返回状态而非伪造数值。"""

        with self._lock:
            metrics = self._last_metrics
        if metrics is None:
            return {"status": "not_available", "message": "尚未执行 Mininet 策略验证。"}
        return {"status": "available", "metrics": metrics.to_dict()}

    def topology_snapshot(self) -> dict[str, object]:
        """返回前端可安全展示的固定拓扑摘要。"""

        return self.topology.to_dict()


def _expect_mapping(payload: object, name: str) -> Mapping[str, object]:
    """确保 HTTP 解码后的请求是对象而非数组或标量。"""

    if not isinstance(payload, Mapping):
        raise IntentError("invalid_request", f"{name}必须是 JSON 对象。")
    return payload


def _reject_unknown_fields(
    data: Mapping[str, object], allowed: frozenset[str], name: str
) -> None:
    """拒绝编译请求携带的伪造 Grounding、动作或命令字段。"""

    unknown = sorted(
        (str(key) for key in data if not isinstance(key, str) or key not in allowed),
    )
    if unknown:
        raise IntentError("unknown_field", f"{name} 含不支持字段：{', '.join(unknown)}。")
