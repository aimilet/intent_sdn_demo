"""意图校验模块：将不可信 JSON 或模型输出转换为经过边界检查的 Intent IR。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import (
    ActorRole,
    Constraint,
    ConstraintMetric,
    ConstraintOperator,
    Intent,
    IntentEnvelope,
    Objective,
    Priority,
    Scope,
    SourceChannel,
    Strength,
    TrafficClass,
)
from intent_sdn_demo.topology import TopologyInventory


MAX_TEXT_LENGTH = 2000
MAX_INTENTS = 10
MAX_VEHICLES_PER_SCOPE = 8
MAX_CONSTRAINTS = 3
MAX_EVIDENCE_LENGTH = 240
MAX_AMBIGUITY_LENGTH = 240


def parse_source_channel(value: object) -> SourceChannel:
    """校验输入通道枚举。"""

    return _parse_enum(SourceChannel, value, "source_channel")


def parse_actor_role(value: object) -> ActorRole:
    """校验由页面提供的角色枚举。"""

    return _parse_enum(ActorRole, value, "actor_role")


def build_envelope(
    *,
    source_channel: SourceChannel,
    actor_role: ActorRole,
    original_text: str,
    intents_payload: object,
    topology: TopologyInventory,
    request_id: str | None = None,
) -> IntentEnvelope:
    """将已选角色和原始输入封装为严格校验后的统一意图集合。"""

    if not isinstance(original_text, str):
        raise IntentError("invalid_text", "原始文本必须是字符串。")
    if len(original_text) > MAX_TEXT_LENGTH:
        raise IntentError("text_too_long", f"原始文本不能超过 {MAX_TEXT_LENGTH} 个字符。")
    if not isinstance(intents_payload, list):
        raise IntentError("invalid_intents", "intents 必须是数组。")
    if not intents_payload:
        raise IntentError("empty_intents", "至少需要一条可执行意图。")
    if len(intents_payload) > MAX_INTENTS:
        raise IntentError("too_many_intents", f"单次请求最多包含 {MAX_INTENTS} 条意图。")

    intents = tuple(_parse_intent(item, topology) for item in intents_payload)
    return IntentEnvelope(
        request_id=request_id or f"req-{uuid4().hex[:12]}",
        source_channel=source_channel,
        actor_role=actor_role,
        original_text=original_text,
        intents=intents,
    )


def envelope_from_dict(payload: object, topology: TopologyInventory) -> IntentEnvelope:
    """校验 API 传回的完整 IR，用于编译接口的二次边界保护。"""

    data = _expect_mapping(payload, "envelope")
    request_id = _required_string(data, "request_id", 64)
    source_channel = parse_source_channel(data.get("source_channel"))
    actor_role = parse_actor_role(data.get("actor_role"))
    original_text = data.get("original_text", "")
    return build_envelope(
        source_channel=source_channel,
        actor_role=actor_role,
        original_text=original_text,
        intents_payload=data.get("intents"),
        topology=topology,
        request_id=request_id,
    )


def _parse_intent(payload: object, topology: TopologyInventory) -> Intent:
    """校验单条意图的结构、枚举、实体、数值和证据。"""

    data = _expect_mapping(payload, "intent")
    scope_data = _expect_mapping(data.get("scope"), "scope")
    vehicle_ids = _parse_vehicle_ids(scope_data.get("vehicle_ids"), topology)
    traffic_class = _parse_enum(TrafficClass, scope_data.get("traffic_class"), "traffic_class")
    if traffic_class is TrafficClass.ALL and vehicle_ids:
        raise IntentError("invalid_scope", "traffic_class 为 all 时不能指定车辆编号。")

    constraints = _parse_constraints(data.get("constraints", []))
    _validate_constraint_set(constraints)
    evidence = _parse_string_list(data.get("evidence"), "evidence", MAX_EVIDENCE_LENGTH, required=True)
    ambiguities = _parse_string_list(
        data.get("ambiguities", []), "ambiguities", MAX_AMBIGUITY_LENGTH, required=False
    )
    return Intent(
        scope=Scope(vehicle_ids=vehicle_ids, traffic_class=traffic_class),
        objective=_parse_enum(Objective, data.get("objective"), "objective"),
        strength=_parse_enum(Strength, data.get("strength"), "strength"),
        priority=_parse_enum(Priority, data.get("priority"), "priority"),
        constraints=constraints,
        evidence=evidence,
        ambiguities=ambiguities,
    )


def _parse_vehicle_ids(value: object, topology: TopologyInventory) -> tuple[str, ...]:
    """校验车辆列表去重后仍在当前固定拓扑内。"""

    if not isinstance(value, list):
        raise IntentError("invalid_vehicle_ids", "vehicle_ids 必须是数组。")
    if len(value) > MAX_VEHICLES_PER_SCOPE:
        raise IntentError("too_many_vehicles", f"单条意图最多指定 {MAX_VEHICLES_PER_SCOPE} 辆车。")
    vehicle_ids: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 64:
            raise IntentError("invalid_vehicle_id", "车辆编号必须是长度不超过 64 的非空字符串。")
        if item not in topology.vehicle_ids:
            raise IntentError("unknown_vehicle", f"当前拓扑不存在车辆：{item}。")
        vehicle_ids.append(item)
    if len(set(vehicle_ids)) != len(vehicle_ids):
        raise IntentError("duplicate_vehicle", "同一作用范围内不能重复指定车辆。")
    return tuple(vehicle_ids)


def _parse_constraints(value: object) -> tuple[Constraint, ...]:
    """校验受支持的数值约束及其单位和比较符。"""

    if not isinstance(value, list):
        raise IntentError("invalid_constraints", "constraints 必须是数组。")
    if len(value) > MAX_CONSTRAINTS:
        raise IntentError("too_many_constraints", f"单条意图最多包含 {MAX_CONSTRAINTS} 个数值约束。")
    constraints: list[Constraint] = []
    for item in value:
        data = _expect_mapping(item, "constraint")
        metric = _parse_enum(ConstraintMetric, data.get("metric"), "constraint.metric")
        operator = _parse_enum(ConstraintOperator, data.get("operator"), "constraint.operator")
        raw_value = data.get("value")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise IntentError("invalid_constraint_value", "约束值必须是正数。")
        numeric_value = float(raw_value)
        if numeric_value <= 0:
            raise IntentError("invalid_constraint_value", "约束值必须大于 0。")
        unit = _required_string(data, "unit", 16)
        _validate_metric_shape(metric, operator, numeric_value, unit)
        constraints.append(Constraint(metric, operator, numeric_value, unit))
    return tuple(constraints)


def _validate_metric_shape(
    metric: ConstraintMetric, operator: ConstraintOperator, value: float, unit: str
) -> None:
    """限定指标与比较符、单位和拓扑能力相匹配。"""

    if metric is ConstraintMetric.LATENCY_MS:
        if operator is not ConstraintOperator.LESS_OR_EQUAL or unit != "ms" or value > 1000:
            raise IntentError("invalid_latency_constraint", "时延约束必须为 latency_ms <= 1..1000 ms。")
    elif metric is ConstraintMetric.MIN_BANDWIDTH_MBPS:
        if operator is not ConstraintOperator.GREATER_OR_EQUAL or unit != "Mbps" or value > 50:
            raise IntentError("invalid_min_bandwidth", "最小带宽必须为 min_bandwidth_mbps >= 1..50 Mbps。")
    elif metric is ConstraintMetric.MAX_BANDWIDTH_MBPS:
        if operator is not ConstraintOperator.LESS_OR_EQUAL or unit != "Mbps" or value > 50:
            raise IntentError("invalid_max_bandwidth", "最大带宽必须为 max_bandwidth_mbps <= 1..50 Mbps。")


def _validate_constraint_set(constraints: tuple[Constraint, ...]) -> None:
    """拒绝单条意图内部自相矛盾的最小和最大带宽约束。"""

    min_values = [
        item.value for item in constraints if item.metric is ConstraintMetric.MIN_BANDWIDTH_MBPS
    ]
    max_values = [
        item.value for item in constraints if item.metric is ConstraintMetric.MAX_BANDWIDTH_MBPS
    ]
    if min_values and max_values and max(min_values) > min(max_values):
        raise IntentError("conflicting_constraints", "同一意图的最小带宽不能大于最大带宽。")


def _parse_string_list(
    value: object, field: str, max_length: int, *, required: bool
) -> tuple[str, ...]:
    """校验证据或歧义文本数组，避免向页面传播过大外部字段。"""

    if not isinstance(value, list):
        raise IntentError(f"invalid_{field}", f"{field} 必须是数组。")
    if required and not value:
        raise IntentError(f"empty_{field}", f"{field} 至少需要一项。")
    if len(value) > MAX_INTENTS:
        raise IntentError(f"too_many_{field}", f"{field} 项数过多。")
    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > max_length:
            raise IntentError(f"invalid_{field}", f"{field} 每一项必须是非空且长度受限的字符串。")
        parsed.append(item.strip())
    return tuple(parsed)


def _parse_enum(enum_type: type, value: object, field: str):
    """将外部枚举文本转换为受限 StrEnum，非法值立即失败。"""

    if not isinstance(value, str):
        raise IntentError("invalid_enum", f"{field} 必须是字符串枚举。")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise IntentError("unsupported_value", f"{field} 不支持 {value!r}，允许值：{allowed}。") from exc


def _expect_mapping(value: object, field: str) -> Mapping[str, object]:
    """确保外部 JSON 对象确为映射结构。"""

    if not isinstance(value, Mapping):
        raise IntentError("invalid_object", f"{field} 必须是对象。")
    return value


def _required_string(data: Mapping[str, object], field: str, max_length: int) -> str:
    """读取长度受限的必填字符串。"""

    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise IntentError("invalid_string", f"{field} 必须是长度不超过 {max_length} 的非空字符串。")
    return value.strip()
