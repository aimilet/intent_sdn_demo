# 车联网通信意图转译与 SDN 策略下发演示

这是一个仅用于本地演示的车联网**通信网络**意图转译项目，不涉及道路交通控制、任务卸载或数字孪生。它将文字、浏览器语音转写或结构化 JSON 转成受限的 `Intent IR`，通过确定性规则完成仲裁和策略选择；只有人工确认后，才在一次性的 Mininet 拓扑中验证固定 OVS 流表和 QoS 动作。

```text
文字 / 语音转写 / JSON → Intent IR → 规则仲裁 → 策略预览 → 人工确认 → Mininet 指标验证
```

详细设计见 [项目方案](docs/plan.md)，实施状态见 [任务表](docs/todo.csv)。

## 本地启动

项目仅使用 Python 标准库，基础模式不需要安装 Python 第三方依赖：

```bash
python3 -m intent_sdn_demo --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。页面提供输入、Intent IR、仲裁与策略预览、拓扑和指标四栏；所有策略均需点击“确认下发并验证”才会执行。

结构化 JSON 可直接使用页面中的五个示例：紧急业务优先、背景视频治理、综合保障、同级硬冲突和非法车辆。前两个正常场景及综合场景会分别选择 `critical_priority`、`congestion_relief`、`combined`；冲突和非法场景应被阻断或拒绝，而不是被猜测修正。

## 文字与语音输入

文字和语音转写文本会调用兼容 Chat Completions 接口的远程模型。启动前在本机终端配置以下环境变量，切勿写入仓库或页面：

```bash
export LLM_BASE_URL='https://模型服务地址'
export LLM_API_KEY='本机私密密钥'
export LLM_MODEL='模型名称'
python3 -m intent_sdn_demo --port 8765
```

模型只做受限 JSON 的语义抽取。JSON 输入不调用模型；无模型配置、模型超时或模型返回非法结构时，文字/语音请求会明确失败，不会退化为关键词猜测。语音按钮使用浏览器内置的语音识别；浏览器不支持或未授权时可手动编辑转写结果。

## Mininet 验证

确认下发默认关闭，避免普通启动意外修改本机网络。要执行真实验证，系统需要已经安装 `mininet`、Open vSwitch 命令行工具和 `iperf`，并以 root 权限启动：

```bash
sudo python3 -m intent_sdn_demo --enable-mininet --port 8765
```

每次确认下发都会创建独立的四车辆、双路径、边缘节点临时拓扑，先在相同的视频压力下采集基线，再应用所选白名单策略并采集策略后指标。执行结束后会清理本次 QoS/队列并停止拓扑；服务不保留持续生效的 OVS 规则。普通模式点击确认会返回 `mininet_disabled`，不会伪造任何指标。

可用接口：

- `GET /api/health`、`GET /api/topology`、`GET /api/metrics`
- `POST /api/intents/parse`
- `POST /api/policies/compile`
- `POST /api/policies/apply`
- `POST /api/policies/reset`

`compile` 支持一个 `envelope` 或多个 `envelopes`。`apply` 只接受当前服务进程中已经预览的 `plan_id`，不接受客户端传入的流表、队列或命令参数。

## 验证

```bash
python3 -m compileall -q intent_sdn_demo tests
python3 -m unittest discover -s tests -v
node --check intent_sdn_demo/web/app.js
```

当前环境已完成单元测试、JavaScript 语法检查和沙箱外的本地 HTTP 回归。`combined` 的真实 Mininet 验收、曾发现的限速缺陷及修复后最终复测均记录在 [集成验收记录](docs/acceptance.md)。当前版本以 OpenFlow 1.3 计量器强制视频 8 Mbps 上限，并在实测超过 8.5 Mbps 时返回失败。
