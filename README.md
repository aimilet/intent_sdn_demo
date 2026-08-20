# 车联网通信意图理解、知识落地与 SDN 决策演示

这是一个仅用于本地演示的车联网**通信网络**意图理解与受控决策项目，不涉及道路交通控制或任务卸载。它将文字、浏览器语音转写或结构化 JSON 转成受限的 Semantic Intent，由本地版本化 SLA 目录完成知识落地，再通过确定性规则完成仲裁、候选评价和稳定选择；只有人工确认后，才在一次性的 Mininet 拓扑中验证固定 OVS 流表和 QoS 动作。

```text
文字 / 语音转写 / JSON → Semantic Intent → SLA Grounding → 规则仲裁 → 候选评价 → 人工确认 → Mininet 指标验证
```

详细设计见 [项目方案](docs/plan.md)，实施状态见 [任务表](docs/todo.csv)。

## 本地启动

项目仅使用 Python 标准库，基础模式不需要安装 Python 第三方依赖：

```bash
python3 -m intent_sdn_demo --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。页面提供输入、语义与知识落地、候选评价、静态拓扑和指标四栏；语义栏分别展示显式/推断要求、SLA 条目标识、版本和派生约束，候选栏展示评价来源、效用分解以及动态 KPI 的可用状态。拓扑会显示四辆业务车辆、RSU、双路径和 Edge 节点，并在策略预览或验证完成后高亮所选路径。Mininet 实测指标优先以卡片展示，完整基线/策略后对比表可展开查看；所有策略均需点击“确认验证”才会执行。

结构化 JSON 可直接使用页面中的五个示例：紧急业务优先、背景视频治理、综合保障、同级硬冲突和非法车辆。前两个正常场景及综合场景会分别选择 `critical_priority`、`congestion_relief`、`combined`；冲突和非法场景应被阻断或拒绝，而不是被猜测修正。

## 文字与语音输入

文字和语音转写文本可以调用 OpenAI Chat Completions 兼容接口或 Ollama 原生 Chat API。推荐在仓库根目录创建已被 `.gitignore` 排除的 `llm-config.local.json`。OpenAI 兼容配置示例：

```json
{
  "provider": "openai",
  "base_url": "https://模型服务地址/v1",
  "api_key": "本机私密密钥",
  "model": "模型名称",
  "timeout_seconds": 30
}
```

不运行本地 Ollama、直接访问 Ollama Cloud 时使用：

```json
{
  "provider": "ollama",
  "base_url": "https://ollama.com",
  "api_key": "Ollama API Key",
  "model": "deepseek-v4-flash:0731",
  "timeout_seconds": 60
}
```

`provider=ollama` 会固定调用 `POST /api/chat`、关闭流式响应并读取 `message.content`，不会再拼接 `/v1/chat/completions`。Cloud API 使用的模型名应以 Ollama 官方 [`/api/tags`](https://ollama.com/api/tags) 返回值为准，不能沿用本地代理的 `:cloud` 别名。Ollama 分支按官方 [Thinking API](https://docs.ollama.com/capabilities/thinking) 发送 `think: false`，并将最大生成量限制为 4096 token，避免短意图在非流式模式下长时间无首字节。Ollama Cloud 当前不支持 structured outputs，因此该分支不发送 `format`；模型输出仍必须通过本项目的 JSON 解析、未知字段、证据和完整 Intent Schema 校验，否则明确拒绝。

然后显式指定该文件启动：

```bash
python3 -m intent_sdn_demo --llm-config ./llm-config.local.json --port 8765
```

配置文件必须是 UTF-8 JSON 对象，只允许上述五个字段，`provider` 只接受 `openai` 或 `ollama`，文件大小不能超过 16 KiB；`base_url` 只接受不含账号、查询参数和片段的 HTTP(S) 地址，超时范围为 `(0, 300]` 秒。远程响应最大读取 1 MiB，API Key 不会写入日志或接口响应。Linux/macOS 上还应执行 `chmod 600 llm-config.local.json`；启动时发现组/其他用户仍可读会明确告警。WSL 的 `/mnt/c`、`/mnt/d` 等 drvfs 挂载可能忽略 `chmod`，此时应将配置移到例如 `/home/用户名/.config/intent-sdn/llm.json` 的 Linux 文件系统目录，再向 `--llm-config` 传入绝对路径。

原有环境变量方式继续支持并默认使用 `openai` 协议；当同时使用 JSON 文件和环境变量时，非空的三个环境变量会分别覆盖文件中的连接字段，`provider` 和 `timeout_seconds` 仍取文件值。启动时会告警被覆盖的字段名，并记录不含密钥的实际 provider、主机、端点、模型和超时。不需要覆盖时应先 `unset LLM_BASE_URL LLM_API_KEY LLM_MODEL`：

```bash
export LLM_BASE_URL='https://模型服务地址'
export LLM_API_KEY='本机私密密钥'
export LLM_MODEL='模型名称'
python3 -m intent_sdn_demo --port 8765
```

模型只做受限 JSON 的语义抽取，可以标记服务类型、显式/推断的定性要求、原文证据和歧义，但不能生成 SLA 数值、Grounding 结果或执行动作。JSON 输入不调用模型；无模型配置、模型超时或模型返回非法结构时，文字/语音请求会明确失败，不会退化为关键词猜测。语音按钮使用浏览器内置的语音识别；浏览器不支持或未授权时可手动编辑转写结果。

服务端会在每次编译时重新校验完整 envelope，并从进程内只读 `SlaCatalog` 重建 Grounding。客户端传入的 SLA 版本、派生约束、动作或命令字段会被拒绝。没有可靠学习模型时，候选中的端到端时延、吞吐、丢包率和利用率预测明确显示为 `not_available`，模板配置能力不会冒充预测或实测。

## Mininet 验证

确认验证默认关闭，避免普通启动意外修改本机网络。要执行真实验证，系统需要已经安装 `mininet`、Open vSwitch 命令行工具和 `iperf`，并以 root 权限启动：

```bash
sudo python3 -m intent_sdn_demo --enable-mininet --port 8765
```

每次确认验证都会创建独立的四车辆、双路径、边缘节点临时拓扑，先在相同的视频压力下采集基线，再应用所选白名单策略并采集策略后指标。执行结束后会清理本次 QoS/队列并停止拓扑；服务不保留持续生效的 OVS 规则。普通模式点击确认会返回 `mininet_disabled`，不会伪造任何指标。

当前 Mininet 是可重复的网络仿真与策略复核沙箱，不与真实网络持续同步，因此不宣称为完整网络数字孪生。项目已经为后续 GNN 评分提供可替换评价接口，但固定的硬约束安全评价不可替换；本阶段不训练 GNN/DRL，也未引入机器学习依赖。

可用接口：

- `GET /api/health`、`GET /api/topology`、`GET /api/metrics`
- `POST /api/intents/parse`
- `POST /api/policies/compile`
- `POST /api/policies/apply`
- `POST /api/policies/reset`

`compile` 支持一个 `envelope` 或多个 `envelopes`。工作台可以按“选择提交角色 → 解析并加入来源批次”的方式累计最多 10 份来源意图，再由服务端完成 Grounding、统一仲裁和预览；确认时只对最终选中的一个白名单 `plan_id` 验证一次。`apply` 不接受客户端传入的流表、队列或命令参数。任意新编译请求都会使旧预览和旧实测缓存失效，阻断结果不能继续确认历史计划。

多方演示时，先选择调度方并加入第一份意图，再切换网络运营方、驾驶员或应用并加入后续意图；批次列表会展示来源和通道，可在编译前移除单份来源。角色选择是 Demo 中的来源标识，不等同于真实身份认证。

## 验证

```bash
python3 -m compileall -q intent_sdn_demo tests
python3 -m unittest discover -s tests -v
node --check intent_sdn_demo/web/app.js
```

当前环境已完成第二版单元测试、JavaScript 语法检查和既有沙箱外本地 HTTP 回归。`combined` 的真实 Mininet 验收、曾发现的限速缺陷及修复后最终复测，以及第二版逻辑回归范围均记录在 [集成验收记录](docs/acceptance.md)。当前版本以 OpenFlow 1.3 计量器强制视频 8 Mbps 上限，并在实测超过 8.5 Mbps 时返回失败。
