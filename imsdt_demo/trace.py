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
    "local": {"x": 18, "y": 44},
    "rsu-primary": {"x": 39, "y": 42},
    "rsu-west": {"x": 20, "y": 25},
    "rsu-south": {"x": 62, "y": 67},
    "edge-primary": {"x": 65, "y": 35},
    "edge-west": {"x": 36, "y": 16},
    "cloud": {"x": 88, "y": 26},
    "twin": {"x": 78, "y": 69},
    "memory": {"x": 91, "y": 72},
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
    vehicles = _fleet(result)
    roads = _roads(result)
    nodes = _nodes(result, vehicles)
    return {
        "scenario": {
            "key": scene.name,
            **SCENARIOS[scene.name],
        },
        "region": {
            "name": "城市快速路边缘协同示范区",
            "area": "2.4 km x 1.6 km",
            "syncMode": "准实时同步",
        },
        "roads": roads,
        "vehicles": vehicles,
        "nodes": nodes,
        "links": _links(result),
        "steps": _steps(result, selected_node, vehicles),
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
            "vehicleCount": len(vehicles),
            "roadCount": len(roads),
            "rsuCount": 3,
            "edgeCount": 2,
        },
    }


def _nodes(result: DemoResult, vehicles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造组件节点和可展示属性。"""

    scene = result.scene
    vehicle_nodes = [
        {
            "id": vehicle["id"],
            "type": "vehicle",
            "label": vehicle["label"],
            "subtitle": vehicle["subtitle"],
            "markerOnly": True,
            "attrs": vehicle["attrs"],
            "x": vehicle["path"][0]["x"],
            "y": vehicle["path"][0]["y"],
        }
        for vehicle in vehicles
    ]
    infra_nodes = [
        {
            "id": "local",
            "type": "compute",
            "label": "本地计算",
            "subtitle": f"{scene.vehicle.vehicle_id} 车载 ECU",
            "attrs": {
                "算力": f"{scene.vehicle.local_compute_ghz:.1f} GHz",
                "传输时延": "0 ms",
                "能耗特征": "计算能耗较高",
            },
            **NODE_POSITIONS["local"],
        },
        {
            "id": "rsu-primary",
            "type": "rsu",
            "label": "RSU-A",
            "subtitle": "主覆盖路侧单元",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps:.1f} Mbps",
                "算力": f"{scene.edge.rsu_compute_ghz:.1f} GHz",
                "队列": str(scene.edge.rsu_queue),
                "利用率": f"{scene.edge.rsu_utilization:.2f}",
                "覆盖车辆": str(max(4, int(scene.vehicle.density * 12))),
            },
            **NODE_POSITIONS["rsu-primary"],
        },
        {
            "id": "rsu-west",
            "type": "rsu",
            "label": "RSU-B",
            "subtitle": "西侧匝道覆盖",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps * 0.78:.1f} Mbps",
                "算力": f"{scene.edge.rsu_compute_ghz * 0.72:.1f} GHz",
                "队列": str(max(1, scene.edge.rsu_queue - 2)),
                "利用率": f"{min(0.96, scene.edge.rsu_utilization + 0.08):.2f}",
                "覆盖车辆": str(max(2, int(scene.vehicle.density * 8))),
            },
            **NODE_POSITIONS["rsu-west"],
        },
        {
            "id": "rsu-south",
            "type": "rsu",
            "label": "RSU-C",
            "subtitle": "南向交叉口覆盖",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps * 0.64:.1f} Mbps",
                "算力": f"{scene.edge.rsu_compute_ghz * 0.66:.1f} GHz",
                "队列": str(scene.edge.rsu_queue + 3),
                "利用率": f"{min(0.98, scene.edge.rsu_utilization + scene.vehicle.density * 0.18):.2f}",
                "覆盖车辆": str(max(3, int(scene.vehicle.density * 10))),
            },
            **NODE_POSITIONS["rsu-south"],
        },
        {
            "id": "edge-primary",
            "type": "edge",
            "label": "边缘云 A",
            "subtitle": "主边缘集群",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps:.1f} Mbps",
                "算力": f"{scene.edge.edge_compute_ghz:.1f} GHz",
                "队列": str(scene.edge.edge_queue),
                "利用率": f"{scene.edge.edge_utilization:.2f}",
                "服务范围": "RSU-A / RSU-C",
            },
            **NODE_POSITIONS["edge-primary"],
        },
        {
            "id": "edge-west",
            "type": "edge",
            "label": "边缘云 B",
            "subtitle": "辅助边缘集群",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps * 0.82:.1f} Mbps",
                "算力": f"{scene.edge.edge_compute_ghz * 0.74:.1f} GHz",
                "队列": str(max(2, scene.edge.edge_queue - 5)),
                "利用率": f"{max(0.20, scene.edge.edge_utilization - 0.11):.2f}",
                "服务范围": "RSU-B",
            },
            **NODE_POSITIONS["edge-west"],
        },
        {
            "id": "cloud",
            "type": "cloud",
            "label": "远端云",
            "subtitle": "Cloud DC",
            "attrs": {
                "算力": "320.0 GHz",
                "链路时延": f"{scene.network.base_latency_ms + 46.0:.1f} ms",
                "适合任务": "非实时批处理",
            },
            **NODE_POSITIONS["cloud"],
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
    return vehicle_nodes + infra_nodes


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
            "to": "rsu-primary",
            "label": "V2I",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps:.1f} Mbps",
                "丢包率": f"{scene.network.packet_loss:.3f}",
                "链路下降风险": f"{result.prediction.link_degradation_risk:.3f}",
            },
        },
        {
            "id": "vehicle-rsu-west",
            "from": "vehicle",
            "to": "rsu-west",
            "label": "V2I 备选",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps * 0.78:.1f} Mbps",
                "链路状态": "可用",
            },
        },
        {
            "id": "vehicle-rsu-south",
            "from": "vehicle",
            "to": "rsu-south",
            "label": "V2I 备选",
            "attrs": {
                "带宽": f"{scene.network.rsu_bandwidth_mbps * 0.64:.1f} Mbps",
                "链路状态": "拥塞风险",
            },
        },
        {
            "id": "rsu-primary-edge-primary",
            "from": "rsu-primary",
            "to": "edge-primary",
            "label": "边缘回传",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps:.1f} Mbps",
                "基础时延": f"{scene.network.base_latency_ms + 8.0:.1f} ms",
            },
        },
        {
            "id": "rsu-west-edge-west",
            "from": "rsu-west",
            "to": "edge-west",
            "label": "边缘回传",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps * 0.82:.1f} Mbps",
                "基础时延": f"{scene.network.base_latency_ms + 11.0:.1f} ms",
            },
        },
        {
            "id": "rsu-south-edge-primary",
            "from": "rsu-south",
            "to": "edge-primary",
            "label": "协同回传",
            "attrs": {
                "带宽": f"{scene.network.edge_bandwidth_mbps * 0.68:.1f} Mbps",
                "基础时延": f"{scene.network.base_latency_ms + 14.0:.1f} ms",
            },
        },
        {
            "id": "edge-primary-twin",
            "from": "edge-primary",
            "to": "twin",
            "label": "状态同步",
            "attrs": {
                "同步质量": f"{result.sync_state.quality:.3f}",
                "状态误差": f"{result.sync_state.state_error:.3f}",
            },
        },
        {
            "id": "edge-west-twin",
            "from": "edge-west",
            "to": "twin",
            "label": "状态同步",
            "attrs": {
                "同步质量": f"{max(0.0, result.sync_state.quality - 0.04):.3f}",
                "状态误差": f"{result.sync_state.state_error + 0.04:.3f}",
            },
        },
        {
            "id": "edge-primary-cloud",
            "from": "edge-primary",
            "to": "cloud",
            "label": "云边协同",
            "attrs": {
                "用途": "非实时回退",
                "基础时延": f"{scene.network.base_latency_ms + 46.0:.1f} ms",
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


def _steps(
    result: DemoResult, selected_node: str, vehicles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """生成决策播放步骤，让前端逐步高亮组件、链路和数据流。"""

    scene = result.scene
    selected_path = _path_for_target(result.selected.plan.target)
    vehicle_ids = [vehicle["id"] for vehicle in vehicles]
    rsu_ids = ["rsu-primary", "rsu-west", "rsu-south"]
    edge_ids = ["edge-primary", "edge-west"]
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
            "summary": f"示范区内 {len(vehicles)} 辆车运行，{scene.vehicle.vehicle_id} 产生 {scene.task.task_type} 任务。",
            "activeNodes": vehicle_ids + rsu_ids + edge_ids,
            "activeLinks": [],
            "details": [
                f"任务大小 {scene.task.size_mb:.2f} MB，计算量 {scene.task.cpu_cycles_g:.2f} G cycles。",
                f"截止时间 {scene.task.deadline_ms:.1f} ms，优先级 {scene.task.priority}。",
                f"道路 {len(_roads(result))} 条，RSU {len(rsu_ids)} 个，边缘节点 {len(edge_ids)} 个。",
                f"主 RSU 利用率 {scene.edge.rsu_utilization:.2f}，主边缘云利用率 {scene.edge.edge_utilization:.2f}。",
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
            "activeNodes": vehicle_ids + rsu_ids + edge_ids + ["twin"],
            "activeLinks": [
                "vehicle-rsu",
                "vehicle-rsu-west",
                "vehicle-rsu-south",
                "rsu-primary-edge-primary",
                "rsu-west-edge-west",
                "rsu-south-edge-primary",
                "edge-primary-twin",
                "edge-west-twin",
            ],
            "details": [
                f"状态延迟 {result.sync_state.data_delay_ms:.1f} ms。",
                f"缺失比例 {result.sync_state.missing_ratio:.2f}。",
                "车辆、RSU、边缘节点和链路状态同步到数字孪生。",
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
            "activeNodes": rsu_ids + edge_ids + ["twin"],
            "activeLinks": ["edge-primary-twin", "edge-west-twin"],
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
            "activeNodes": ["vehicle", "local", "rsu-primary", "edge-primary", "twin"],
            "activeLinks": [
                "vehicle-local",
                "vehicle-rsu",
                "rsu-primary-edge-primary",
                "edge-primary-cloud",
            ],
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
            "activeNodes": ["local", "rsu-primary", "edge-primary", "twin"],
            "activeLinks": [
                "vehicle-local",
                "vehicle-rsu",
                "rsu-primary-edge-primary",
                "edge-primary-twin",
            ],
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
            "activeLinks": selected_path + ["edge-primary-twin"],
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
            "activeLinks": selected_path + ["edge-primary-twin", "twin-memory"],
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
        Target.RSU: "rsu-primary",
        Target.EDGE: "edge-primary",
    }[target]


def _path_for_target(target: Target) -> list[str]:
    """返回目标策略对应的数据流链路。"""

    if target == Target.LOCAL:
        return ["vehicle-local"]
    if target == Target.RSU:
        return ["vehicle-rsu"]
    return ["vehicle-rsu", "rsu-primary-edge-primary"]


def _vehicle_path() -> list[dict[str, float]]:
    """车辆在页面中的演示轨迹，按步骤索引推进。"""

    return [
        {"x": 8, "y": 73},
        {"x": 14, "y": 71},
        {"x": 20, "y": 69},
        {"x": 27, "y": 67},
        {"x": 34, "y": 64},
        {"x": 41, "y": 62},
        {"x": 48, "y": 60},
        {"x": 55, "y": 59},
        {"x": 62, "y": 61},
        {"x": 69, "y": 64},
    ]


def _roads(result: DemoResult) -> list[dict[str, Any]]:
    """生成多道路网络，拥塞程度随场景变化。"""

    density = result.scene.vehicle.density
    high_load = result.scene.name == "high_load"
    emergency = result.scene.name == "emergency"
    return [
        {
            "id": "main-east",
            "label": "主干道 E-W",
            "x1": 5,
            "y1": 72,
            "x2": 94,
            "y2": 58,
            "lanes": 3,
            "congestion": min(0.98, density + (0.12 if high_load else 0.0)),
        },
        {
            "id": "north-avenue",
            "label": "北侧辅路",
            "x1": 7,
            "y1": 31,
            "x2": 79,
            "y2": 22,
            "lanes": 2,
            "congestion": min(0.90, density * 0.74 + (0.10 if high_load else 0.0)),
        },
        {
            "id": "south-cross",
            "label": "南向交叉口",
            "x1": 62,
            "y1": 90,
            "x2": 55,
            "y2": 18,
            "lanes": 2,
            "congestion": min(0.96, density * 0.86 + (0.08 if high_load else 0.0)),
        },
        {
            "id": "west-ramp",
            "label": "西侧匝道",
            "x1": 15,
            "y1": 88,
            "x2": 26,
            "y2": 18,
            "lanes": 1,
            "congestion": min(0.92, density * 0.68),
        },
        {
            "id": "emergency-lane",
            "label": "应急车道",
            "x1": 10,
            "y1": 78,
            "x2": 73,
            "y2": 68,
            "lanes": 1,
            "congestion": 0.18 if emergency else min(0.65, density * 0.42),
        },
    ]


def _fleet(result: DemoResult) -> list[dict[str, Any]]:
    """生成示范区车辆队列，焦点车辆参与真实决策，其他车辆提供环境上下文。"""

    scene = result.scene
    focus_type = "emergency" if scene.name == "emergency" else "focus"
    return [
        {
            "id": "vehicle",
            "label": scene.vehicle.vehicle_id,
            "subtitle": "焦点任务车辆",
            "role": "focus",
            "vehicleType": focus_type,
            "path": _vehicle_path(),
            "attrs": {
                "任务": scene.task.task_type,
                "任务大小": f"{scene.task.size_mb:.2f} MB",
                "截止时间": f"{scene.task.deadline_ms:.1f} ms",
                "优先级": str(scene.task.priority),
                "速度": f"{scene.vehicle.speed_mps:.1f} m/s",
                "电量": f"{scene.vehicle.battery_percent:.1f}%",
            },
        },
        {
            "id": "veh-014",
            "label": "veh-014",
            "subtitle": "普通感知任务",
            "role": "background",
            "vehicleType": "car",
            "path": _straight_path(14, 76, 78, 64, drift=-2),
            "attrs": {
                "任务": "map_update",
                "任务大小": "0.80 MB",
                "截止时间": "220.0 ms",
                "优先级": "3",
                "速度": "11.8 m/s",
                "电量": "73.0%",
            },
        },
        {
            "id": "veh-027",
            "label": "veh-027",
            "subtitle": "乘客娱乐任务",
            "role": "background",
            "vehicleType": "car",
            "path": _straight_path(65, 88, 56, 23, drift=1),
            "attrs": {
                "任务": "infotainment",
                "任务大小": "2.40 MB",
                "截止时间": "450.0 ms",
                "优先级": "2",
                "速度": "9.6 m/s",
                "电量": "64.0%",
            },
        },
        {
            "id": "veh-033",
            "label": "veh-033",
            "subtitle": "协同感知任务",
            "role": "background",
            "vehicleType": "truck",
            "path": _straight_path(9, 33, 70, 24, drift=2),
            "attrs": {
                "任务": "cooperative_perception",
                "任务大小": "1.70 MB",
                "截止时间": "180.0 ms",
                "优先级": "6",
                "速度": "8.4 m/s",
                "电量": "81.0%",
            },
        },
        {
            "id": "veh-041",
            "label": "veh-041",
            "subtitle": "低电量车辆",
            "role": "background",
            "vehicleType": "ev",
            "path": _straight_path(25, 86, 31, 26, drift=-1),
            "attrs": {
                "任务": "diagnostics",
                "任务大小": "0.60 MB",
                "截止时间": "260.0 ms",
                "优先级": "4",
                "速度": "10.1 m/s",
                "电量": "19.0%",
            },
        },
        {
            "id": "veh-052",
            "label": "veh-052",
            "subtitle": "边缘缓存请求",
            "role": "background",
            "vehicleType": "car",
            "path": _straight_path(39, 70, 91, 58, drift=-1),
            "attrs": {
                "任务": "cache_prefetch",
                "任务大小": "1.10 MB",
                "截止时间": "300.0 ms",
                "优先级": "3",
                "速度": "13.2 m/s",
                "电量": "58.0%",
            },
        },
    ]


def _straight_path(
    start_x: float, start_y: float, end_x: float, end_y: float, *, drift: float
) -> list[dict[str, float]]:
    """按 10 个播放步骤生成背景车辆移动轨迹。"""

    points: list[dict[str, float]] = []
    for index in range(10):
        ratio = index / 9
        wave = (index % 3 - 1) * drift * 0.28
        points.append(
            {
                "x": start_x + (end_x - start_x) * ratio,
                "y": start_y + (end_y - start_y) * ratio + wave,
            }
        )
    return points
