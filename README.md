# IMSDT-VEC 演示版

本目录提供一个轻量 Python demo，用于验证 `docs/todo.csv` 中最小可行版本的主链路：

```text
场景生成 -> 意图解析 -> 冲突消解 -> 多智能体候选方案 -> 数字孪生评估
       -> 历史案例检索/写回 -> 执行结果模拟 -> 指标输出
```

## 运行方式

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m imsdt_demo --scenario emergency
```

可选场景：

- `normal`：普通低时延任务。
- `low_energy`：车辆低电量，偏向低能耗。
- `emergency`：紧急安全任务，偏向低时延和高可靠。
- `high_load`：边缘节点高负载，验证同步预测和历史记忆链路。

运行测试：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest discover
```

默认会将历史案例写入 `data/history_cases.json`。如只想观察单次决策，可使用：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m imsdt_demo --scenario emergency --no-save
```
