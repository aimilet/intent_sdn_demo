/* 本文件驱动本地四栏工作台；所有动态文本以 textContent 渲染，不解释为 HTML。 */

"use strict";

const state = {
  channel: "json",
  envelope: null,
  decision: null,
  topology: null,
};

const elements = {
  actorRole: document.querySelector("#actor-role"),
  status: document.querySelector("#status"),
  jsonPayload: document.querySelector("#json-payload"),
  textPayload: document.querySelector("#text-payload"),
  voicePayload: document.querySelector("#voice-payload"),
  scenario: document.querySelector("#scenario"),
  irOutput: document.querySelector("#ir-output"),
  decisionSummary: document.querySelector("#decision-summary"),
  decisionOutput: document.querySelector("#decision-output"),
  topologyOutput: document.querySelector("#topology-output"),
  metricsOutput: document.querySelector("#metrics-output"),
  compileButton: document.querySelector("#compile-policy"),
  applyButton: document.querySelector("#apply-policy"),
};

const scenarios = {
  combined: {
    intents: [
      intent("emergency", "prioritize_traffic", "must", "critical", ["veh-emergency-01"], "救护车消息必须优先"),
      intent("video", "limit_background_traffic", "prefer", "normal", [], "视频可降级"),
    ],
  },
  critical: {
    intents: [
      intent("emergency", "minimize_latency", "must", "critical", ["veh-emergency-01"], "紧急消息必须低时延"),
    ],
  },
  congestion: {
    intents: [
      intent("video", "relieve_network_congestion", "must", "high", [], "视频流量需迁移并限速"),
    ],
  },
  conflict: {
    intents: [
      intent("video", "prioritize_traffic", "must", "critical", [], "视频必须最高优先级"),
      intent("video", "limit_background_traffic", "must", "critical", [], "视频必须限速"),
    ],
  },
  invalid: {
    intents: [
      intent("emergency", "minimize_latency", "must", "critical", ["veh-unknown"], "未知车辆应被拒绝"),
    ],
  },
};

function intent(trafficClass, objective, strength, priority, vehicleIds, evidence) {
  return {
    scope: { vehicle_ids: vehicleIds, traffic_class: trafficClass },
    objective,
    strength,
    priority,
    constraints: [],
    evidence: [evidence],
    ambiguities: [],
  };
}

function setStatus(message, kind = "normal") {
  elements.status.textContent = message;
  elements.status.dataset.kind = kind;
}

async function request(path, method = "GET", body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json; charset=utf-8";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || "本地服务返回了未知错误。");
  }
  return payload;
}

function activateChannel(channel) {
  state.channel = channel;
  document.querySelectorAll(".tab").forEach((button) => {
    const selected = button.dataset.channel === channel;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".channel-content").forEach((content) => {
    content.classList.toggle("hidden", content.id !== `${channel}-input`);
  });
}

function currentPayload() {
  if (state.channel === "json") {
    try {
      return JSON.parse(elements.jsonPayload.value);
    } catch {
      throw new Error("JSON 示例或输入不是合法 JSON，无法解析。");
    }
  }
  const value = state.channel === "text" ? elements.textPayload.value : elements.voicePayload.value;
  if (!value.trim()) {
    throw new Error("请先输入或完成语音转写。 ");
  }
  return value.trim();
}

async function parseIntent() {
  try {
    setStatus("正在校验并转译意图…");
    const envelope = await request("/api/intents/parse", "POST", {
      source_channel: state.channel,
      actor_role: elements.actorRole.value,
      payload: currentPayload(),
    });
    state.envelope = envelope;
    state.decision = null;
    elements.irOutput.textContent = JSON.stringify(envelope, null, 2);
    elements.decisionOutput.textContent = "尚未编译。";
    elements.decisionSummary.textContent = "Intent IR 已通过校验，可进入规则仲裁。";
    elements.compileButton.disabled = false;
    elements.applyButton.disabled = true;
    setStatus(`解析完成：${envelope.intents.length} 条意图。`, "success");
  } catch (error) {
    state.envelope = null;
    state.decision = null;
    elements.compileButton.disabled = true;
    elements.applyButton.disabled = true;
    elements.irOutput.textContent = `解析失败：${error.message}`;
    setStatus("解析被拒绝，未进入策略阶段。", "error");
  }
}

async function compilePolicy() {
  if (!state.envelope) {
    return;
  }
  try {
    setStatus("正在执行规则仲裁与候选择优…");
    const decision = await request("/api/policies/compile", "POST", { envelope: state.envelope });
    state.decision = decision;
    elements.decisionOutput.textContent = JSON.stringify(decision, null, 2);
    elements.decisionSummary.textContent = decision.selection_reason;
    elements.applyButton.disabled = decision.status !== "ready" || !decision.selected_plan;
    setStatus(decision.status === "ready" ? "策略已预览，等待人工确认下发。" : "策略被规则阻断。", decision.status === "ready" ? "success" : "error");
  } catch (error) {
    elements.applyButton.disabled = true;
    elements.decisionSummary.textContent = `编译失败：${error.message}`;
    setStatus("策略编译失败。", "error");
  }
}

async function applyPolicy() {
  const planId = state.decision?.selected_plan?.plan_id;
  if (!planId) {
    return;
  }
  try {
    elements.applyButton.disabled = true;
    setStatus("正在创建临时 Mininet 拓扑并采集前后指标…");
    const result = await request("/api/policies/apply", "POST", { plan_id: planId });
    renderMetrics(result.metrics);
    setStatus(`验证完成：已确认下发 ${planId}。`, "success");
  } catch (error) {
    setStatus(`未下发策略：${error.message}`, "error");
  } finally {
    elements.applyButton.disabled = false;
  }
}

async function resetPolicy() {
  try {
    const result = await request("/api/policies/reset", "POST", {});
    state.decision = null;
    elements.applyButton.disabled = true;
    elements.decisionSummary.textContent = result.message;
    elements.metricsOutput.textContent = "已重置；不存在可展示的策略验证指标。";
    setStatus("已清除策略预览与指标缓存。", "success");
  } catch (error) {
    setStatus(`重置失败：${error.message}`, "error");
  }
}

function renderTopology(topology) {
  const profiles = Object.entries(topology.traffic_profiles)
    .map(([trafficClass, profile]) => `<li><strong>${trafficClass}</strong> · ${profile.vehicle_id} · UDP ${profile.udp_port}</li>`)
    .join("");
  elements.topologyOutput.innerHTML = `
    <div class="path-card low"><strong>低时延路径</strong><span>${topology.paths.low_latency.bandwidth_mbps} Mbps · ${topology.paths.low_latency.delay_ms} ms</span></div>
    <div class="path-card high"><strong>高容量路径</strong><span>${topology.paths.high_capacity.bandwidth_mbps} Mbps · ${topology.paths.high_capacity.delay_ms} ms</span></div>
    <ul>${profiles}</ul>`;
}

function metricRow(label, baseline, applied, unit = "") {
  const before = baseline === null || baseline === undefined ? "无样本" : `${baseline}${unit}`;
  const after = applied === null || applied === undefined ? "无样本" : `${applied}${unit}`;
  return `<tr><th>${label}</th><td>${before}</td><td>${after}</td></tr>`;
}

function renderMetrics(snapshot) {
  const rows = [
    metricRow("紧急业务 P95 时延", snapshot.baseline.emergency_p95_latency_ms, snapshot.applied.emergency_p95_latency_ms, " ms"),
    ...Object.keys(snapshot.applied.throughput_mbps).map((traffic) => metricRow(`${traffic} 吞吐`, snapshot.baseline.throughput_mbps[traffic], snapshot.applied.throughput_mbps[traffic], " Mbps")),
    ...Object.keys(snapshot.applied.packet_loss_percent).map((traffic) => metricRow(`${traffic} 丢包`, snapshot.baseline.packet_loss_percent[traffic], snapshot.applied.packet_loss_percent[traffic], " %")),
    ...Object.keys(snapshot.applied.link_utilization_percent).map((path) => metricRow(`${path} 利用率`, snapshot.baseline.link_utilization_percent[path], snapshot.applied.link_utilization_percent[path], " %")),
  ];
  elements.metricsOutput.innerHTML = `<p class="metric-title">计划：${snapshot.plan_id}</p><table><thead><tr><th>指标</th><th>基线</th><th>策略后</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function loadScenario() {
  elements.jsonPayload.value = JSON.stringify(scenarios[elements.scenario.value], null, 2);
  activateChannel("json");
  setStatus("已载入示例，尚未提交解析。 ");
}

function startVoiceRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    setStatus("此浏览器不支持语音识别；请手动填写转写文本。", "error");
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = (event) => {
    elements.voicePayload.value = event.results[0][0].transcript;
    setStatus("语音已转写，可解析意图。", "success");
  };
  recognition.onerror = () => setStatus("语音转写未完成；请检查浏览器授权或手动输入。", "error");
  recognition.start();
  setStatus("正在监听语音…");
}

async function initialize() {
  loadScenario();
  try {
    state.topology = await request("/api/topology");
    renderTopology(state.topology);
    const metrics = await request("/api/metrics");
    if (metrics.status === "available") {
      renderMetrics(metrics.metrics);
    }
    setStatus("本地工作台已就绪。", "success");
  } catch (error) {
    setStatus(`无法连接本地服务：${error.message}`, "error");
  }
}

document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => activateChannel(button.dataset.channel)));
document.querySelector("#load-scenario").addEventListener("click", loadScenario);
document.querySelector("#start-voice").addEventListener("click", startVoiceRecognition);
document.querySelector("#parse-intent").addEventListener("click", parseIntent);
elements.compileButton.addEventListener("click", compilePolicy);
elements.applyButton.addEventListener("click", applyPolicy);
document.querySelector("#reset-policy").addEventListener("click", resetPolicy);
initialize();
