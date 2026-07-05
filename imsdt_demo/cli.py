"""CLI 模块：提供 demo 的命令行参数解析和中文结果输出。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from imsdt_demo.pipeline import DemoResult, run_demo


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数并运行演示流程。"""

    parser = argparse.ArgumentParser(description="IMSDT-VEC 轻量演示")
    parser.add_argument(
        "--scenario",
        choices=["normal", "low_energy", "emergency", "high_load"],
        default="emergency",
        help="演示场景",
    )
    parser.add_argument("--seed", type=int, default=7, help="随机种子")
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("data/history_cases.json"),
        help="历史案例 JSON 文件路径",
    )
    parser.add_argument("--no-save", action="store_true", help="不写入历史案例文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = parser.parse_args(argv)

    result = run_demo(
        scenario=args.scenario,
        history_path=args.history,
        seed=args.seed,
        save_history=not args.no_save,
    )
    if args.json:
        print(json.dumps(_json_summary(result), ensure_ascii=False, indent=2))
    else:
        print(_text_summary(result))
    return 0


def _json_summary(result: DemoResult) -> dict[str, object]:
    """生成适合脚本消费的 JSON 摘要。"""

    selected = result.selected
    return {
        "scenario": result.scene.name,
        "intent_count": len(result.intents),
        "memory_hits": result.memory_hits,
        "selected_target": selected.plan.target.value,
        "latency_ms": round(selected.latency_ms, 3),
        "energy_j": round(selected.energy_j, 3),
        "reliability": round(selected.reliability, 5),
        "intent_satisfaction": round(selected.intent_satisfaction, 5),
        "sla_violation": selected.sla_violation,
        "prediction": asdict(result.prediction),
        "batch": {
            "task_count": len(result.batch_schedule.queue),
            "average_latency_ms": round(result.batch_schedule.average_latency_ms, 3),
            "total_energy_j": round(result.batch_schedule.total_energy_j, 3),
            "average_satisfaction": round(result.batch_schedule.average_satisfaction, 5),
            "sla_violation_rate": round(result.batch_schedule.sla_violation_rate, 5),
            "target_counts": result.batch_schedule.target_counts,
        },
        "case_count": result.case_count,
    }


def _text_summary(result: DemoResult) -> str:
    """生成可读的中文命令行摘要。"""

    lines = [
        f"场景: {result.scene.name}",
        f"任务: {result.scene.task.task_id} / {result.scene.task.task_type}",
        f"同步质量: {result.sync_state.quality:.3f}",
        (
            "未来风险: "
            f"过载 {result.prediction.overload_risk:.3f}, "
            f"超时 {result.prediction.timeout_risk:.3f}, "
            f"链路下降 {result.prediction.link_degradation_risk:.3f}"
        ),
        f"历史命中: {result.memory_hits}",
        (
            "批量调度: "
            f"{len(result.batch_schedule.queue)} 个任务, "
            f"平均时延 {result.batch_schedule.average_latency_ms:.1f} ms, "
            f"违约率 {result.batch_schedule.sla_violation_rate:.2f}"
        ),
        "",
        "候选方案:",
        "target   source  latency_ms  energy_j  reliability  satisfaction  risk   violation",
    ]
    for item in sorted(result.evaluations, key=lambda data: data.intent_satisfaction, reverse=True):
        lines.append(
            f"{item.plan.target.value:<8} "
            f"{item.plan.source:<6} "
            f"{item.latency_ms:>10.1f} "
            f"{item.energy_j:>8.2f} "
            f"{item.reliability:>11.3f} "
            f"{item.intent_satisfaction:>12.3f} "
            f"{item.risk_score:>5.3f} "
            f"{str(item.sla_violation):>9}"
        )

    selected = result.selected
    lines.extend(
        [
            "",
            f"选择方案: {selected.plan.target.value} ({selected.plan.source})",
            f"选择原因: {selected.explanation}",
            (
                "执行反馈: "
                f"实际时延 {result.execution.latency_ms:.1f} ms, "
                f"实际满足度 {result.execution.intent_satisfaction:.3f}, "
                f"预测误差 {result.execution.prediction_error:.3f}"
            ),
            f"历史案例数: {result.case_count}",
        ]
    )
    return "\n".join(lines)
