# 车联网通信意图转译与 SDN 策略下发演示

项目已停止维护旧的任务卸载、数字孪生和历史记忆 Demo，并进入新版工程重构阶段。旧实现已从当前工作树移除，但可通过 Git 历史恢复。

新版将聚焦于以下闭环：

```text
文字 / 语音转写 / JSON → Intent IR → 规则仲裁 → SDN 策略 → Mininet 验证
```

详细设计见 [项目方案](docs/plan.md)，实施顺序见 [任务表](docs/todo.csv)。

## 当前可运行能力

现阶段已经实现 Intent IR 校验、结构化 JSON 输入、远程模型文本抽取接口、多来源冲突仲裁和策略预览；Mininet 实际下发与四栏页面仍按任务表后续阶段推进。

启动仅本机可访问的 API 服务：

```bash
python3 -m intent_sdn_demo --port 8765
```

可用接口：

- `GET /api/health`
- `GET /api/topology`
- `POST /api/intents/parse`
- `POST /api/policies/compile`

`/api/policies/compile` 可接收一个 `envelope`，也可接收多个 `envelopes` 进行跨来源仲裁。

文字或语音转写输入需要配置兼容接口的 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。结构化 JSON 输入不依赖远程模型。

运行回归测试：

```bash
python3 -m unittest discover -v
```
