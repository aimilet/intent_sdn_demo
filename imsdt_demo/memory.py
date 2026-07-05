"""历史记忆模块：负责案例签名、相似检索、策略复用、写回和置信度更新。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from imsdt_demo.models import (
    CandidatePlan,
    EvaluationResult,
    ExecutionResult,
    HistoryCase,
    IntentProfile,
    PredictionResult,
    SceneState,
    Target,
)


def scene_signature(scene: SceneState, prediction: PredictionResult) -> dict[str, float]:
    """提取用于历史案例相似度计算的归一化场景特征。"""

    return {
        "density": scene.vehicle.density,
        "battery": scene.vehicle.battery_percent / 100.0,
        "task_size": scene.task.size_mb / 25.0,
        "task_cycles": scene.task.cpu_cycles_g / 10.0,
        "deadline": scene.task.deadline_ms / 250.0,
        "priority": scene.task.priority / 10.0,
        "rsu_bandwidth": scene.network.rsu_bandwidth_mbps / 120.0,
        "edge_bandwidth": scene.network.edge_bandwidth_mbps / 100.0,
        "rsu_utilization": scene.edge.rsu_utilization,
        "edge_utilization": scene.edge.edge_utilization,
        "future_overload": prediction.overload_risk,
        "future_timeout": prediction.timeout_risk,
    }


def intent_vector(profile: IntentProfile) -> dict[str, float]:
    """将意图画像转换为可比较向量。"""

    return dict(profile.weights)


def similarity(left: dict[str, float], right: dict[str, float]) -> float:
    """计算两个归一化向量的平均相似度，缺失字段按最大差异处理。"""

    keys = set(left) | set(right)
    if not keys:
        return 0.0
    distance = 0.0
    for key in keys:
        distance += abs(left.get(key, 0.0) - right.get(key, 0.0))
    return max(0.0, 1.0 - distance / len(keys))


class HistoryStore:
    """历史案例库：使用 JSON 文件持久化，便于 demo 直接查看和复现。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.cases: list[HistoryCase] = []
        if path and path.exists():
            self.cases = self._load(path)

    def query(
        self,
        scene: SceneState,
        profile: IntentProfile,
        prediction: PredictionResult,
        top_k: int = 5,
    ) -> list[tuple[HistoryCase, float]]:
        """返回满足阈值的相似历史案例，用于候选策略复用。"""

        signature = scene_signature(scene, prediction)
        vector = intent_vector(profile)
        scored: list[tuple[HistoryCase, float]] = []
        for case in self.cases:
            scene_sim = similarity(signature, case.scene_signature)
            intent_sim = similarity(vector, case.intent_vector)
            score = scene_sim * 0.70 + intent_sim * 0.30
            if (
                score >= 0.82
                and case.confidence >= 0.65
                and case.prediction_error <= 0.20
            ):
                scored.append((case, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def reusable_plans(
        self,
        scene: SceneState,
        profile: IntentProfile,
        prediction: PredictionResult,
    ) -> list[CandidatePlan]:
        """将可靠历史案例转换为候选策略，并标记来源为 memory。"""

        plans: list[CandidatePlan] = []
        for case, score in self.query(scene, profile, prediction):
            plan = case.plan
            plans.append(
                CandidatePlan(
                    plan_id=f"{scene.task.task_id}-memory-{plan.target.value}",
                    target=plan.target,
                    bandwidth_mbps=plan.bandwidth_mbps,
                    compute_share=plan.compute_share,
                    queue_priority=max(plan.queue_priority, scene.task.priority),
                    source="memory",
                    explanation=f"复用历史案例 {case.case_id[:8]}，相似度 {score:.2f}。",
                )
            )
        return plans

    def add_case(
        self,
        scene: SceneState,
        profile: IntentProfile,
        prediction: PredictionResult,
        evaluation: EvaluationResult,
        execution: ExecutionResult,
    ) -> HistoryCase:
        """写入新案例，置信度由执行误差和硬约束结果共同决定。"""

        confidence = max(0.1, 1.0 - execution.prediction_error)
        if execution.sla_violation:
            confidence *= 0.72
        case = HistoryCase(
            case_id=str(uuid4()),
            scene_signature=scene_signature(scene, prediction),
            intent_vector=intent_vector(profile),
            plan=evaluation.plan,
            evaluation=evaluation,
            execution=execution,
            prediction_error=execution.prediction_error,
            confidence=confidence,
            timestamp_ms=scene.tick_ms,
            tags=[scene.name, evaluation.plan.target.value],
        )
        self.cases.append(case)
        return case

    def save(self) -> None:
        """持久化案例库，目录不存在时创建目录。"""

        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [case.to_json_dict() for case in self.cases]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self, path: Path) -> list[HistoryCase]:
        """从 JSON 文件恢复历史案例，并校验核心字段类型。"""

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("历史案例文件必须是列表结构。")
        cases: list[HistoryCase] = []
        for item in raw:
            plan_data = item["plan"]
            plan = CandidatePlan(
                plan_id=plan_data["plan_id"],
                target=Target(plan_data["target"]),
                bandwidth_mbps=float(plan_data["bandwidth_mbps"]),
                compute_share=float(plan_data["compute_share"]),
                queue_priority=int(plan_data["queue_priority"]),
                source=plan_data["source"],
                explanation=plan_data["explanation"],
            )
            evaluation = self._evaluation_from_json(item["evaluation"], plan)
            execution = ExecutionResult(**item["execution"])
            cases.append(
                HistoryCase(
                    case_id=item["case_id"],
                    scene_signature={key: float(value) for key, value in item["scene_signature"].items()},
                    intent_vector={key: float(value) for key, value in item["intent_vector"].items()},
                    plan=plan,
                    evaluation=evaluation,
                    execution=execution,
                    prediction_error=float(item["prediction_error"]),
                    confidence=float(item["confidence"]),
                    timestamp_ms=int(item["timestamp_ms"]),
                    tags=list(item.get("tags", [])),
                )
            )
        return cases

    def _evaluation_from_json(
        self, data: dict[str, object], plan: CandidatePlan
    ) -> EvaluationResult:
        """恢复评估结果，避免 JSON 内嵌 plan 与外层 plan 反序列化不一致。"""

        return EvaluationResult(
            plan=plan,
            latency_ms=float(data["latency_ms"]),
            energy_j=float(data["energy_j"]),
            reliability=float(data["reliability"]),
            resource_cost=float(data["resource_cost"]),
            intent_satisfaction=float(data["intent_satisfaction"]),
            sla_violation=bool(data["sla_violation"]),
            risk_score=float(data["risk_score"]),
            explanation=str(data["explanation"]),
        )

    def extend(self, cases: Iterable[HistoryCase]) -> None:
        """测试和批处理使用的批量追加入口。"""

        self.cases.extend(cases)
