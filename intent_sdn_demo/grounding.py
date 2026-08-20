"""SLA 知识落地模块：从进程内只读版本目录重建 Grounding 证据。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import (
    Constraint,
    ConstraintMetric,
    ConstraintOperator,
    Intent,
    GroundingRecord,
    ServiceType,
    TrafficClass,
)


_PREFERENCE_METRICS = frozenset({"latency", "bandwidth", "reliability", "reconfiguration_cost"})


@dataclass(frozen=True)
class SlaProfile:
    """一条版本化 SLA 目录项；创建后不可修改。"""

    service: ServiceType
    profile_id: str
    profile_version: str
    derived_constraints: tuple[Constraint, ...]
    preference_order: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        """提供只读目录的安全展示结构。"""

        return {
            "service": self.service.value,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "derived_constraints": [item.to_dict() for item in self.derived_constraints],
            "preference_order": list(self.preference_order),
            "reason": self.reason,
        }


# 交通类别到服务的映射与输入校验保持一致；这里独立维护可避免 Grounding 依赖请求字段。
_SERVICE_BY_TRAFFIC_CLASS = {
    TrafficClass.EMERGENCY: ServiceType.EMERGENCY_V2X,
    TrafficClass.CONTROL: ServiceType.VEHICLE_CONTROL,
    TrafficClass.NAVIGATION: ServiceType.NAVIGATION,
    TrafficClass.VIDEO: ServiceType.BACKGROUND_VIDEO,
}


class SlaCatalog:
    """进程内只读 SLA 目录，仅允许服务端用它生成派生约束和证据。"""

    def __init__(self, profiles: Mapping[object, object] | None = None) -> None:
        """校验目录项并冻结映射，拒绝运行时通过请求内容修改 SLA。"""

        source = _default_profiles() if profiles is None else profiles
        normalized: dict[ServiceType, SlaProfile] = {}
        for raw_key, raw_profile in source.items():
            service = _parse_service(raw_key)
            profile = _coerce_profile(service, raw_profile)
            if service in normalized:
                raise ValueError(f"SLA 目录重复服务：{service.value}")
            normalized[service] = profile
        if set(normalized) != set(ServiceType):
            missing = ", ".join(item.value for item in set(ServiceType) - set(normalized))
            raise ValueError(f"SLA 目录缺少服务：{missing}")
        self._profiles = MappingProxyType(normalized)

    @property
    def profiles(self) -> Mapping[ServiceType, SlaProfile]:
        """返回不可写的服务到 SLA 条目映射。"""

        return self._profiles

    def profile_for(self, service: ServiceType | str) -> SlaProfile:
        """按固定服务枚举读取目录项，未命中时 fail-fast。"""

        parsed = _parse_service(service)
        try:
            return self._profiles[parsed]
        except KeyError as exc:
            raise IntentError("sla_not_found", f"没有服务 {parsed.value!r} 的 SLA 条目。", 422) from exc

    def ground(self, intent: Intent) -> GroundingRecord:
        """仅使用目录内容重建一条意图的 Grounding 记录。"""

        service = intent.service or _SERVICE_BY_TRAFFIC_CLASS.get(intent.scope.traffic_class)
        if service is None:
            raise IntentError("missing_service", "无法为 all 业务范围确定 SLA 服务。", 422)
        profile = self.profile_for(service)
        conflicts = _find_constraint_conflicts(intent.constraints, profile)
        return GroundingRecord(
            service=service,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            derived_constraints=profile.derived_constraints,
            preference_order=profile.preference_order,
            reason=profile.reason,
            conflicts=tuple(conflicts),
        )

    def ground_envelopes(self, envelopes: tuple[object, ...]) -> tuple[GroundingRecord, ...]:
        """按 envelope 与意图输入顺序生成证据，确保响应可稳定追溯。"""

        records: list[GroundingRecord] = []
        for envelope in envelopes:
            for intent in envelope.intents:
                records.append(self.ground(intent))
        return tuple(records)


def default_sla_catalog() -> SlaCatalog:
    """返回固定 Demo SLA 目录；调用方只能读取，不能通过 API 覆盖。"""

    return SlaCatalog()


def _default_profiles() -> dict[ServiceType, SlaProfile]:
    """构造本地版本化 SLA 常量，所有数值均来自目录而非外部输入。"""

    return {
        ServiceType.EMERGENCY_V2X: SlaProfile(
            ServiceType.EMERGENCY_V2X,
            "sla:emergency_v2x",
            "1",
            (
                Constraint(ConstraintMetric.LATENCY_MS, ConstraintOperator.LESS_OR_EQUAL, 20, "ms"),
                Constraint(ConstraintMetric.MIN_BANDWIDTH_MBPS, ConstraintOperator.GREATER_OR_EQUAL, 12, "Mbps"),
            ),
            ("latency", "bandwidth", "reconfiguration_cost"),
            "固定应急 V2X 服务等级条目",
        ),
        ServiceType.VEHICLE_CONTROL: SlaProfile(
            ServiceType.VEHICLE_CONTROL,
            "sla:vehicle_control",
            "1",
            (),
            ("latency", "reliability", "bandwidth", "reconfiguration_cost"),
            "固定车辆控制服务等级条目",
        ),
        ServiceType.NAVIGATION: SlaProfile(
            ServiceType.NAVIGATION,
            "sla:navigation",
            "1",
            (),
            ("latency", "bandwidth", "reconfiguration_cost"),
            "固定导航服务等级条目",
        ),
        ServiceType.BACKGROUND_VIDEO: SlaProfile(
            ServiceType.BACKGROUND_VIDEO,
            "sla:background_video",
            "1",
            (
                Constraint(ConstraintMetric.MAX_BANDWIDTH_MBPS, ConstraintOperator.LESS_OR_EQUAL, 8, "Mbps"),
            ),
            ("bandwidth", "reconfiguration_cost"),
            "固定背景视频服务等级条目",
        ),
    }


def _coerce_profile(service: ServiceType, raw: object) -> SlaProfile:
    """允许测试注入受控目录项，同时对字段和类型做完整校验。"""

    if isinstance(raw, SlaProfile):
        if raw.service is not service:
            raise ValueError("SLA 条目 service 与目录键不一致。")
        _validate_profile(raw)
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError("SLA 条目必须是 SlaProfile 或对象。")
    try:
        profile = SlaProfile(
            service=service,
            profile_id=raw["profile_id"],
            profile_version=raw["profile_version"],
            derived_constraints=tuple(raw["derived_constraints"]),
            preference_order=tuple(raw["preference_order"]),
            reason=raw["reason"],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("SLA 条目字段不完整。") from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: SlaProfile) -> None:
    """校验只读目录中的标识、约束和偏好顺序，防止配置错误进入评价器。"""

    if not profile.profile_id or len(profile.profile_id) > 80:
        raise ValueError("SLA profile_id 无效。")
    if not profile.profile_version or len(profile.profile_version) > 32:
        raise ValueError("SLA profile_version 无效。")
    if not profile.reason or len(profile.reason) > 240:
        raise ValueError("SLA reason 无效。")
    for constraint in profile.derived_constraints:
        if not isinstance(constraint, Constraint):
            raise ValueError("SLA 派生约束必须是 Constraint。")
    if not profile.preference_order or len(profile.preference_order) > 8:
        raise ValueError("SLA preference_order 无效。")
    if len(set(profile.preference_order)) != len(profile.preference_order):
        raise ValueError("SLA preference_order 不能重复。")
    if any(item not in _PREFERENCE_METRICS for item in profile.preference_order):
        raise ValueError("SLA preference_order 含不支持指标。")


def _parse_service(value: object) -> ServiceType:
    """将目录键或服务字段转换为固定枚举。"""

    if isinstance(value, ServiceType):
        return value
    if isinstance(value, str):
        try:
            return ServiceType(value)
        except ValueError as exc:
            raise ValueError(f"不支持的 SLA 服务：{value}") from exc
    raise TypeError("SLA 服务必须是 ServiceType 或字符串。")


def _find_constraint_conflicts(
    explicit: tuple[Constraint, ...], profile: SlaProfile
) -> list[str]:
    """保留显式约束优先级，并标出与 SLA 派生上下界不可同时满足的冲突。"""

    sla_source = f"SLA 条目 {profile.profile_id}@{profile.profile_version}"
    constraints = [(item, "显式输入") for item in explicit]
    constraints.extend((item, sla_source) for item in profile.derived_constraints)
    minimums = [
        (item, source)
        for item, source in constraints
        if item.metric is ConstraintMetric.MIN_BANDWIDTH_MBPS
    ]
    maximums = [
        (item, source)
        for item, source in constraints
        if item.metric is ConstraintMetric.MAX_BANDWIDTH_MBPS
    ]
    if not minimums or not maximums:
        return []
    conflicts: list[str] = []
    for minimum, minimum_source in minimums:
        for maximum, maximum_source in maximums:
            if minimum.value <= maximum.value:
                continue
            conflicts.append(
                "显式约束与 SLA 派生约束冲突："
                f"{minimum_source} 要求 {minimum.metric.value} >= {minimum.value:g}，"
                f"{maximum_source} 要求 {maximum.metric.value} <= {maximum.value:g}。"
            )
    return conflicts
