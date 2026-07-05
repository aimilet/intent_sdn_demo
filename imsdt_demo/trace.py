"""前端轨迹导出模块：将 Python 决策闭环转换为可视化页面消费的结构化数据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from imsdt_demo.models import EvaluationResult, Target
from imsdt_demo.pipeline import DemoResult, run_demo


SCENARIOS = {
    "normal": {
        "title": "普通低时延",
        "focus": "低时延",
        "description": "车辆产生普通感知任务，系统优先降低任务完成时延。",
    },
    "low_energy": {
        "title": "低能耗",
        "focus": "低能耗",
        "description": "车辆电量较低，系统在时延可接受前提下降低车辆侧能耗。",
    },
    "emergency": {
        "title": "紧急优先",
        "focus": "紧急任务",
        "description": "紧急感知任务触发硬时延和高可靠约束。",
    },
    "high_load": {
        "title": "高负载",
        "focus": "负载保护",
        "description": "RSU 与边缘云接近过载，系统需要避开拥塞资源。",
    },
}


NODE_POSITIONS = {
    "vehicle": {"x": 16, "y": 68},
    "local": {"x": 22, "y": 48},
    "rsu": {"x": 42, "y": 42},
    "edge": {"x": 68, "y": 38},
    "twin": {"x": 72, "y": 70},
    "memory": {"x": 90, "y": 70},
}


def build_visual_trace(
    scenario: str,
    *,
    seed: int = 7,
    history_path: Path | None = None,
    save_history: bool = False,
) -> dict[str, Any]:
    """生成前端可视化轨迹，包含组件、链路、步骤、候选评估和执行反馈。"""

    result = run_demo(
        scenario=scenario,
        seed=seed,
        history_path=history_path,
        save_history=save_history,
    )
    return _trace_from_result(result)


def _trace_from_result(result: DemoResult) -> dict[str, Any]:
    """从 demo 结果组装页面数据，保持所有数值来自后端决策链路。"""

    scene = result.scene
    selected_node = _node_for_target(result.selected.plan.target)
    return {
        "scenario": {
            "key": scene.name,
            **SCENARIOS[scene.name],
        },
        "vehiclePath": _vehicle_path(),
        "nodes": _nodes(result),
        "links": _links(result),
        "steps": _steps(result, selected_node),
        "evaluations": [_evaluation(item, result.selected) for item in result.evaluations],
        "selected": {
            "target": result.selected.plan.target.value,
            "node": selected_node,
            "source": result.selected.plan.source,
            "explanation": result.selected.explanation,
        },
        "execution": {
            "latencyMs": round(result.execution.latency_ms, 3),
            "energyJ": round(result.execution.energy_j, 3),
            "reliability": round(result.execution.reliability, 5),
            "satisfaction": round(result.execution.intent_satisfaction, 5),
            "predictionError": round(result.execution.prediction_error, 5),
            "slaViolation": result.execution.sla_violation,
        },
        "summary": {
            "memoryHits": result.memory_hits,
            "caseCount": result.case_count,
            "syncQuality": round(result.sync_state.quality, 5),
            "predictionConfidence": round(result.prediction.confidence, 5),
            "dominantIntent": result.profile.dominant_intent.value,
        },
    }


def _nodes(result: DemoResult) -> list[dict[str, Any]]:
    """构造组件节点和可展示属性。"""

    scene = result.scene
    return [
        {
            "id": "vehicle",
            "type": "vehicle",
            "label": "车辆",
            "subtitle": scene.vehicle.vehicle_id,
            "attrs": {
                "位置": f"{scene.vehicle.position_m:.1f} m",
                "速度": f"{scene.vehicle.speed_mps:.1f} m/s",
                "电量": f"{scene.vehicle.battery_percent:.1f}%",
                "本地算力": f"{scene.vehicle.local_compute_ghz:.1f} GHz",
                "车辆密度": f"{scene.vehicle.density:.2f}",
            },
            **NODE_POSITIONS["vehicle"],
        },
        {
            "id": "local",
            "type": "compute",
            "label": "本地计算",
            "subtitle": "车载 ECU",
            "attrs": {
                "算力": f"{scene.vehicle.local_compute_ghz:.1f} GHz",
                "传输时延": "0 ms",
                "能耗特征": "计算能耗较高",
            },
            **NODE_POSITIONS["local"],
        },
        {
            "id": "rsu",
            "type": "rsu",
            "label": "路侧单元",
            "subtitle": "RSU-01",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps:.1f} Mbps",
                "算力": f"{scene.edge.rsu_compute_ghz:.1f} GHz",
                "队列": str(scene.edge.rsu_queue),
                "利用率": f"{scene.edge.rsu_utilization:.2f}",
            },
            **NODE_POSITIONS["rsu"],
        },
        {
            "id": "edge",
            "type": "edge",
            "label": "边缘云",
            "subtitle": "Edge-Cluster",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps:.1f} Mbps",
                "算力": f"{scene.edge.edge_compute_ghz:.1f} GHz",
                "队列": str(scene.edge.edge_queue),
                "利用率": f"{scene.edge.edge_utilization:.2f}",
            },
            **NODE_POSITIONS["edge"],
        },
        {
            "id": "twin",
            "type": "twin",
            "label": "数字孪生",
            "subtitle": "同步 + 预测 + 评估",
            "attrs": {
                "同步质量": f"{result.sync_state.quality:.3f}",
                "数据延迟": f"{result.sync_state.data_delay_ms:.1f} ms",
                "预测窗口": f"{result.prediction.window_s} s",
                "预测置信度": f"{result.prediction.confidence:.3f}",
            },
            **NODE_POSITIONS["twin"],
        },
        {
            "id": "memory",
            "type": "memory",
            "label": "历史记忆",
            "subtitle": "案例库",
            "attrs": {
                "命中数": str(result.memory_hits),
                "案例总数": str(result.case_count),
                "复用策略": "相似度 + 置信度 + 误差阈值",
            },
            **NODE_POSITIONS["memory"],
        },
    ]


def _links(result: DemoResult) -> list[dict[str, Any]]:
    """构造组件链路，属性来自当前场景和预测结果。"""

    scene = result.scene
    return [
        {
            "id": "vehicle-local",
            "from": "vehicle",
            "to": "local",
            "label": "本地总线",
            "attrs": {"时延": "0 ms", "可靠性": "0.985"},
        },
        {
            "id": "vehicle-rsu",
            "from": "vehicle",
            "to": "rsu",
            "label": "V2I",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps:.1f} Mbps",
                "丢包率": f"{scene.network.packet_loss:.3f}",
                "链路下降风险": f"{result.prediction.link_degradation_risk:.3f}",
            },
        },
        {
            "id": "rsu-edge",
            "from": "rsu",
            "to": "edge",
            "label": "边缘回传",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps:.1f} Mbps",
                "基础时延": f"{scene.network.base_latency_ms + 8.0:.1f} ms",
            },
        },
        {
            "id": "edge-twin",
            "from": "edge",
            "to": "twin",
            "label": "状态同步",
            "attrs": {
                "同步质量": f"{result.sync_state.quality:.3f}",
                "状态误差": f"{result.sync_state.state_error:.3f}",
            },
        },
        {
            "id": "twin-memory",
            "from": "twin",
            "to": "memory",
            "label": "案例检索",
            "attrs": {
                "命中": str(result.memory_hits),
                "案例": str(result.case_count),
            },
        },
        {
            "id": "twin-vehicle",
            "from": "twin",
            "to": "vehicle",
            "label": "策略下发",
            "attrs": {"目标": result.selected.plan.target.value},
        },
    ]


def _steps(result: DemoResult, selected_node: str) -> list[dict[str, Any]]:
    """生成决策播放步骤，让前端逐步高亮组件、链路和数据流。"""

    scene = result.scene
    selected_path = _path_for_target(result.selected.plan.target)
    candidate_text = [
        (
            f"{item.plan.target.value}: 时延 {item.latency_ms:.1f} ms，"
            f"能耗 {item.energy_j:.2f} J，满足度 {item.intent_satisfaction:.3f}"
        )
        for item in sorted(result.evaluations, key=lambda data: data.intent_satisfaction, reverse=True)
    ]
    return [
        {
            "id": "state",
            "title": "场景状态",
            "summary": f"{scene.vehicle.vehicle_id} 产生 {scene.task.task_type} 任务。",
            "activeNodes": ["vehicle", "rsu", "edge"],
            "activeLinks": [],
            "details": [
                f"任务大小 {scene.task.size_mb:.2f} MB，计算量 {scene.task.cpu_cycles_g:.2f} G cycles。",
                f"截止时间 {scene.task.deadline_ms:.1f} ms，优先级 {scene.task.priority}。",
                f"RSU 利用率 {scene.edge.rsu_utilization:.2f}，边缘云利用率 {scene.edge.edge_utilization:.2f}。",
            ],
            "metrics": {
                "车辆电量": scene.vehicle.battery_percent,
                "任务优先级": scene.task.priority * 10,
                "车辆密度": scene.vehicle.density * 100,
            },
        },
        {
            "id": "intent",
            "title": "意图解析",
            "summary": f"识别 {len(result.intents)} 条显式/隐式意图，主导意图为 {result.profile.dominant_intent.value}。",
            "activeNodes": ["vehicle", "twin"],
            "activeLinks": ["twin-vehicle"],
            "details": [intent.reason for intent in result.intents],
            "metrics": {key: value * 100 for key, value in result.profile.weights.items()},
        },
        {
            "id": "sync",
            "title": "实时同步",
            "summary": f"同步质量 {result.sync_state.quality:.3f}，状态误差 {result.sync_state.state_error:.3f}。",
            "activeNodes": ["vehicle", "rsu", "edge", "twin"],
            "activeLinks": ["vehicle-rsu", "rsu-edge", "edge-twin"],
            "details": [
                f"状态延迟 {result.sync_state.data_delay_ms:.1f} ms。",
                f"缺失比例 {result.sync_state.missing_ratio:.2f}。",
            ],
            "metrics": {
                "同步质量": result.sync_state.quality * 100,
                "状态新鲜度": max(0.0, 100.0 - result.sync_state.data_delay_ms / 10.0),
            },
        },
        {
            "id": "predict",
            "title": "未来预测",
            "summary": f"预测未来 {result.prediction.window_s} 秒的过载、超时和链路风险。",
            "activeNodes": ["rsu", "edge", "twin"],
            "activeLinks": ["edge-twin"],
            "details": [
                f"未来 RSU 利用率 {result.prediction.future_rsu_utilization:.3f}。",
                f"未来边缘云利用率 {result.prediction.future_edge_utilization:.3f}。",
                f"任务到达率 {result.prediction.task_arrival_rate:.3f}。",
            ],
            "metrics": {
                "过载风险": result.prediction.overload_risk * 100,
                "超时风险": result.prediction.timeout_risk * 100,
                "链路风险": result.prediction.link_degradation_risk * 100,
            },
        },
        {
            "id": "memory",
            "title": "历史检索",
            "summary": f"历史案例命中 {result.memory_hits} 条，案例库当前 {result.case_count} 条。",
            "activeNodes": ["twin", "memory"],
            "activeLinks": ["twin-memory"],
            "details": [
                "按意图、任务、网络、资源和未来风险计算相似度。",
                "命中案例只作为候选策略，仍需经过数字孪生评估。",
            ],
            "metrics": {
                "历史命中": min(result.memory_hits * 25, 100),
                "案例规模": min(result.case_count * 10, 100),
            },
        },
        {
            "id": "generate",
            "title": "候选生成",
            "summary": "任务卸载、资源分配和约束检查智能体生成候选方案。",
            "activeNodes": ["vehicle", "local", "rsu", "edge", "twin"],
            "activeLinks": ["vehicle-local", "vehicle-rsu", "rsu-edge"],
            "details": candidate_text,
            "metrics": {
                "候选数": len(result.evaluations) * 20,
                "可行候选": sum(not item.sla_violation for item in result.evaluations) * 25,
            },
        },
        {
            "id": "evaluate",
            "title": "孪生评估",
            "summary": "数字孪生评估每个候选的时延、能耗、可靠性和意图满足度。",
            "activeNodes": ["local", "rsu", "edge", "twin"],
            "activeLinks": ["vehicle-local", "vehicle-rsu", "rsu-edge", "edge-twin"],
            "details": [item.explanation for item in result.evaluations],
            "metrics": {
                "最高满足度": result.selected.intent_satisfaction * 100,
                "最低风险": (1.0 - min(item.risk_score for item in result.evaluations)) * 100,
            },
        },
        {
            "id": "select",
            "title": "策略选择",
            "summary": f"选择 {result.selected.plan.target.value}，来源 {result.selected.plan.source}。",
            "activeNodes": ["twin", selected_node],
            "activeLinks": selected_path + ["edge-twin"],
            "details": [
                result.selected.explanation,
                result.selected.plan.explanation,
            ],
            "metrics": {
                "意图满足度": result.selected.intent_satisfaction * 100,
                "可靠性": result.selected.reliability * 100,
                "风险余量": (1.0 - result.selected.risk_score) * 100,
            },
        },
        {
            "id": "execute",
            "title": "执行下发",
            "summary": f"策略下发到 {result.selected.plan.target.value} 执行，任务开始流转。",
            "activeNodes": ["vehicle", selected_node],
            "activeLinks": selected_path + ["twin-vehicle"],
            "details": [
                f"实际时延 {result.execution.latency_ms:.1f} ms。",
                f"实际可靠性 {result.execution.reliability:.3f}。",
            ],
            "metrics": {
                "实际满足度": result.execution.intent_satisfaction * 100,
                "预测误差": result.execution.prediction_error * 100,
            },
        },
        {
            "id": "feedback",
            "title": "反馈更新",
            "summary": "执行结果写回历史案例库，供后续相似场景复用。",
            "activeNodes": ["vehicle", "twin", "memory"],
            "activeLinks": selected_path + ["edge-twin", "twin-memory"],
            "details": [
                f"案例库更新后共 {result.case_count} 条。",
                f"预测误差 {result.execution.prediction_error:.3f}。",
            ],
            "metrics": {
                "案例置信基础": max(0.0, 100.0 - result.execution.prediction_error * 100.0),
                "历史规模": min(result.case_count * 10, 100),
            },
        },
    ]


def _evaluation(item: EvaluationResult, selected: EvaluationResult) -> dict[str, Any]:
    """转换候选评估结果为前端表格行。"""

    return {
        "target": item.plan.target.value,
        "source": item.plan.source,
        "latencyMs": round(item.latency_ms, 3),
        "energyJ": round(item.energy_j, 3),
        "reliability": round(item.reliability, 5),
        "resourceCost": round(item.resource_cost, 5),
        "satisfaction": round(item.intent_satisfaction, 5),
        "risk": round(item.risk_score, 5),
        "violation": item.sla_violation,
        "selected": item.plan.plan_id == selected.plan.plan_id and item.plan.source == selected.plan.source,
    }


def _node_for_target(target: Target) -> str:
    """将执行目标映射到前端节点。"""

    return {
        Target.LOCAL: "local",
        Target.RSU: "rsu",
        Target.EDGE: "edge",
    }[target]


def _path_for_target(target: Target) -> list[str]:
    """返回目标策略对应的数据流链路。"""

    if target == Target.LOCAL:
        return ["vehicle-local"]
    if target == Target.RSU:
        return ["vehicle-rsu"]
    return ["vehicle-rsu", "rsu-edge"]


def _vehicle_path() -> list[dict[str, float]]:
    """车辆在页面中的演示轨迹，按步骤索引推进。"""

    return [
        {"x": 10, "y": 72},
        {"x": 14, "y": 70},
        {"x": 18, "y": 68},
        {"x": 22, "y": 66},
        {"x": 25, "y": 64},
        {"x": 28, "y": 63},
        {"x": 32, "y": 64},
        {"x": 36, "y": 66},
        {"x": 40, "y": 68},
        {"x": 44, "y": 70},
    ]
