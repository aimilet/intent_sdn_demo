/* 本文件驱动本地四栏工作台；拓扑使用固定 SVG 结构，外部数据只作为受控文本和数字填充。 */

"use strict";

const state = {
  channel: "json",
  envelopes: [],
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
  envelopeList: document.querySelector("#envelope-list"),
  clearBatchButton: document.querySelector("#clear-batch"),
  irOutput: document.querySelector("#ir-output"),
  decisionSummary: document.querySelector("#decision-summary"),
  decisionOutput: document.querySelector("#decision-output"),
  topologyOutput: document.querySelector("#topology-output"),
  metricsOutput: document.querySelector("#metrics-output"),
  compileButton: document.querySelector("#compile-policy"),
  applyButton: document.querySelector("#apply-policy"),
};

const roleLabels = {
  dispatcher: "调度方",
  operator: "网络运营方",
  driver: "驾驶员",
  application: "应用",
};

const channelLabels = {
  json: "JSON",
  text: "文字",
  voice: "语音转写",
};

const trafficLabels = {
  emergency: "紧急业务",
  control: "控制业务",
  navigation: "导航业务",
  video: "背景视频",
};

const pathLabels = {
  low_latency: "低时延路径",
  high_capacity: "高容量路径",
};

const planLabels = {
  baseline: "基线示意",
  critical_priority: "紧急优先",
  congestion_relief: "视频治理",
  combined: "综合保障",
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

function renderEnvelopeBatch() {
  elements.envelopeList.replaceChildren();
  if (state.envelopes.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-state";
    empty.textContent = "尚未加入来源意图。";
    elements.envelopeList.appendChild(empty);
    elements.compileButton.disabled = true;
    elements.irOutput.textContent = "等待解析…";
    return;
  }
  state.envelopes.forEach((envelope, index) => {
    const item = document.createElement("li");
    item.className = "envelope-item";
    const summary = document.createElement("span");
    summary.textContent = `${roleLabels[envelope.actor_role] || envelope.actor_role} · ${channelLabels[envelope.source_channel] || envelope.source_channel} · ${envelope.intents.length} 条意图`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "envelope-remove";
    remove.textContent = "移除";
    remove.addEventListener("click", () => removeEnvelope(index));
    item.append(summary, remove);
    elements.envelopeList.appendChild(item);
  });
  elements.irOutput.textContent = JSON.stringify(state.envelopes, null, 2);
  elements.compileButton.disabled = false;
}

function invalidateDecision() {
  state.decision = null;
  elements.decisionOutput.textContent = "尚未编译。";
  elements.decisionSummary.textContent = state.envelopes.length
    ? "来源意图已更新，请重新执行规则仲裁。"
    : "完成解析后可进行规则仲裁与候选择优。";
  elements.applyButton.disabled = true;
}

function removeEnvelope(index) {
  state.envelopes.splice(index, 1);
  renderEnvelopeBatch();
  invalidateDecision();
  setStatus("已移除一个来源意图，请重新编译。", "normal");
}

function clearEnvelopeBatch() {
  state.envelopes = [];
  renderEnvelopeBatch();
  invalidateDecision();
  setStatus("已清空待汇总来源。", "normal");
}

async function parseIntent() {
  try {
    if (state.envelopes.length >= 10) {
      throw new Error("单次汇总最多包含 10 份来源意图。");
    }
    setStatus("正在校验并转译意图…");
    const envelope = await request("/api/intents/parse", "POST", {
      source_channel: state.channel,
      actor_role: elements.actorRole.value,
      payload: currentPayload(),
    });
    state.envelopes.push(envelope);
    renderEnvelopeBatch();
    invalidateDecision();
    setStatus(
      `已加入第 ${state.envelopes.length} 个来源：${roleLabels[envelope.actor_role] || envelope.actor_role}，${envelope.intents.length} 条意图。`,
      "success",
    );
  } catch (error) {
    elements.irOutput.textContent = state.envelopes.length
      ? `${JSON.stringify(state.envelopes, null, 2)}\n\n本次加入失败：${error.message}`
      : `解析失败：${error.message}`;
    setStatus("解析被拒绝，未进入策略阶段。", "error");
  }
}

async function compilePolicy() {
  if (state.envelopes.length === 0) {
    return;
  }
  try {
    setStatus("正在执行规则仲裁与候选择优…");
    const decision = await request("/api/policies/compile", "POST", { envelopes: state.envelopes });
    state.decision = decision;
    if (state.topology) {
      renderTopology(
        state.topology,
        decision.selected_plan?.plan_id || "baseline",
        decision.selected_plan ? "preview" : "baseline",
      );
    }
    elements.decisionOutput.textContent = JSON.stringify(decision, null, 2);
    elements.decisionSummary.textContent = decision.selection_reason;
    elements.applyButton.disabled = decision.status !== "ready" || !decision.selected_plan;
    setStatus(decision.status === "ready" ? "策略已预览，等待人工确认下发。" : "策略被规则阻断。", decision.status === "ready" ? "success" : "error");
  } catch (error) {
    elements.applyButton.disabled = true;
    elements.decisionSummary.textContent = `编译失败：${error.message}`;
    if (state.topology) {
      renderTopology(state.topology, "baseline", "baseline");
    }
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
    if (state.topology) {
      renderTopology(state.topology, result.plan_id, "applied");
    }
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
    clearEnvelopeBatch();
    if (state.topology) {
      renderTopology(state.topology, "baseline", "baseline");
    }
    elements.decisionSummary.textContent = result.message;
    elements.metricsOutput.textContent = "已重置；不存在可展示的策略验证指标。";
    setStatus("已清除策略预览与指标缓存。", "success");
  } catch (error) {
    setStatus(`重置失败：${error.message}`, "error");
  }
}

function finiteNumber(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function escapeXml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}

// 路径标注只读取固定计划映射；车辆业务与计划之外的输入不会参与图形布局。
function pathForTraffic(planId, trafficClass) {
  if (trafficClass === "video" && ["congestion_relief", "combined"].includes(planId)) {
    return "high_capacity";
  }
  return "low_latency";
}

// 拓扑是可复现的静态示意，策略阶段只改变状态标签、路径高亮和车辆业务指向。
function renderTopology(topology, planId = "baseline", phase = "baseline") {
  const lowPath = topology?.paths?.low_latency || {};
  const highPath = topology?.paths?.high_capacity || {};
  const lowActive = Object.keys(trafficLabels).some((trafficClass) => pathForTraffic(planId, trafficClass) === "low_latency");
  const highActive = Object.keys(trafficLabels).some((trafficClass) => pathForTraffic(planId, trafficClass) === "high_capacity");
  const phaseLabel = phase === "applied" ? "策略后" : phase === "preview" ? "策略预览" : "基线";
  const pathState = (active) => active ? "active" : "inactive";
  const vehicle = (trafficClass, y, symbol) => {
    const profile = topology?.traffic_profiles?.[trafficClass];
    const route = pathForTraffic(planId, trafficClass);
    const label = trafficLabels[trafficClass];
    const vehicleId = escapeXml(profile?.vehicle_id || "固定车辆");
    const udpPort = profile?.udp_port || "-";
    return `
      <g class="vehicle-node ${trafficClass}" transform="translate(28 ${y})">
        <rect class="vehicle-body" x="0" y="14" width="168" height="54" rx="12"></rect>
        <path class="vehicle-cabin" d="M24 14 L42 0 H86 L104 14 Z"></path>
        <circle class="vehicle-wheel" cx="38" cy="70" r="8"></circle>
        <circle class="vehicle-wheel" cx="126" cy="70" r="8"></circle>
        <text class="vehicle-symbol" x="17" y="48">${symbol}</text>
        <text class="vehicle-label" x="49" y="37">${label}</text>
        <text class="vehicle-meta" x="49" y="55">${vehicleId} · UDP ${udpPort}</text>
        <text class="vehicle-route" x="49" y="83">→ ${pathLabels[route]}</text>
      </g>`;
  };

  elements.topologyOutput.innerHTML = `
    <div class="topology-toolbar">
      <span class="topology-phase">${phaseLabel}：${planLabels[planId] || "固定计划"}</span>
      <span class="topology-hint">高亮路径表示计划选择，利用率见下方卡片</span>
    </div>
    <svg class="topology-svg" viewBox="0 0 960 430" role="img" aria-label="四车辆、RSU、双路径和边缘节点拓扑">
      <defs>
        <marker id="arrow-low" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#1976d2"></path></marker>
        <marker id="arrow-high" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#009688"></path></marker>
        <marker id="arrow-muted" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#aab7c7"></path></marker>
      </defs>
      <g class="vehicle-links">
        <line x1="196" y1="75" x2="350" y2="205"></line>
        <line x1="196" y1="165" x2="350" y2="205"></line>
        <line x1="196" y1="255" x2="350" y2="205"></line>
        <line x1="196" y1="345" x2="350" y2="205"></line>
      </g>
      <g class="path-group low-path ${pathState(lowActive)}">
        <line class="path-link" x1="480" y1="205" x2="555" y2="125" marker-end="url(#arrow-low)"></line>
        <line class="path-link" x1="700" y1="125" x2="800" y2="205" marker-end="url(#arrow-low)"></line>
      </g>
      <g class="path-group high-path ${pathState(highActive)}">
        <line class="path-link" x1="480" y1="225" x2="555" y2="315" marker-end="url(#arrow-high)"></line>
        <line class="path-link" x1="700" y1="315" x2="800" y2="225" marker-end="url(#arrow-high)"></line>
      </g>
      ${vehicle("emergency", 38, "+")}
      ${vehicle("control", 128, "⚙")}
      ${vehicle("navigation", 218, "✦")}
      ${vehicle("video", 308, "▶")}
      <g class="network-node rsu-node">
        <rect x="350" y="170" width="130" height="70" rx="14"></rect>
        <text x="415" y="201">RSU</text>
        <text x="415" y="222">OpenFlow 1.3</text>
      </g>
      <g class="network-node switch-node low-switch">
        <rect x="555" y="95" width="145" height="60" rx="12"></rect>
        <text x="627" y="121">低时延交换机</text>
        <text x="627" y="140">${finiteNumber(lowPath.bandwidth_mbps, 20)} Mbps · ${finiteNumber(lowPath.delay_ms, 5)} ms</text>
      </g>
      <g class="network-node switch-node high-switch">
        <rect x="555" y="285" width="145" height="60" rx="12"></rect>
        <text x="627" y="311">高容量交换机</text>
        <text x="627" y="330">${finiteNumber(highPath.bandwidth_mbps, 50)} Mbps · ${finiteNumber(highPath.delay_ms, 15)} ms</text>
      </g>
      <g class="network-node edge-node">
        <rect x="800" y="170" width="130" height="70" rx="14"></rect>
        <text x="865" y="201">Edge</text>
        <text x="865" y="222">业务服务节点</text>
      </g>
    </svg>
    <div class="path-summary">
      <div class="path-card low ${pathState(lowActive)}"><strong>低时延路径</strong><span>${finiteNumber(lowPath.bandwidth_mbps, 20)} Mbps · ${finiteNumber(lowPath.delay_ms, 5)} ms</span></div>
      <div class="path-card high ${pathState(highActive)}"><strong>高容量路径</strong><span>${finiteNumber(highPath.bandwidth_mbps, 50)} Mbps · ${finiteNumber(highPath.delay_ms, 15)} ms</span></div>
    </div>`;
}

function metricRow(label, baseline, applied, unit = "") {
  const before = baseline === null || baseline === undefined ? "无样本" : `${baseline}${unit}`;
  const after = applied === null || applied === undefined ? "无样本" : `${applied}${unit}`;
  return `<tr><th>${label}</th><td>${before}</td><td>${after}</td></tr>`;
}

function renderMetrics(snapshot) {
  // 卡片突出关键结果，完整表格仍保留基线与策略后的逐项核对数据。
  const rows = [
    metricRow("紧急业务 P95 时延", snapshot.baseline.emergency_p95_latency_ms, snapshot.applied.emergency_p95_latency_ms, " ms"),
    ...Object.keys(snapshot.applied.throughput_mbps).map((traffic) => metricRow(`${trafficLabels[traffic] || traffic} 吞吐`, snapshot.baseline.throughput_mbps[traffic], snapshot.applied.throughput_mbps[traffic], " Mbps")),
    ...Object.keys(snapshot.applied.packet_loss_percent).map((traffic) => metricRow(`${trafficLabels[traffic] || traffic} 丢包`, snapshot.baseline.packet_loss_percent[traffic], snapshot.applied.packet_loss_percent[traffic], " %")),
    ...Object.keys(snapshot.applied.link_utilization_percent).map((path) => metricRow(`${pathLabels[path] || path} 利用率`, snapshot.baseline.link_utilization_percent[path], snapshot.applied.link_utilization_percent[path], " %")),
  ];
  const cards = [
    ["紧急 P95 时延", snapshot.baseline.emergency_p95_latency_ms, snapshot.applied.emergency_p95_latency_ms, " ms", "emergency"],
    ...Object.keys(snapshot.applied.throughput_mbps).map((traffic) => [
      `${trafficLabels[traffic] || traffic} 吞吐`,
      snapshot.baseline.throughput_mbps[traffic],
      snapshot.applied.throughput_mbps[traffic],
      " Mbps",
      traffic,
      snapshot.baseline.packet_loss_percent[traffic],
      snapshot.applied.packet_loss_percent[traffic],
    ]),
    ...Object.keys(snapshot.applied.link_utilization_percent).map((path) => [
      `${pathLabels[path] || path} 利用率`,
      snapshot.baseline.link_utilization_percent[path],
      snapshot.applied.link_utilization_percent[path],
      " %",
      "path",
    ]),
  ];
  elements.metricsOutput.innerHTML = `<p class="metric-title">计划：${planLabels[snapshot.plan_id] || snapshot.plan_id}</p><div class="metric-cards"></div><details class="metric-details"><summary>查看完整指标表</summary><table><thead><tr><th>指标</th><th>基线</th><th>策略后</th></tr></thead><tbody>${rows.join("")}</tbody></table></details>`;
  const cardContainer = elements.metricsOutput.querySelector(".metric-cards");
  cards.forEach(([label, baseline, applied, unit, category, baselineLoss, appliedLoss]) => {
    const card = document.createElement("article");
    card.className = `metric-card ${category}`;
    const before = baseline === null || baseline === undefined ? "无样本" : `${baseline}${unit}`;
    const after = applied === null || applied === undefined ? "无样本" : `${applied}${unit}`;
    const detail = document.createElement("p");
    detail.className = "metric-card-detail";
    detail.textContent = category === "path"
      ? `基线 ${before} · 策略后 ${after}`
      : `基线 ${before} · 策略后 ${after}${baselineLoss === undefined ? "" : ` · 丢包 ${baselineLoss}% → ${appliedLoss}%`}`;
    const title = document.createElement("h3");
    title.textContent = label;
    const value = document.createElement("strong");
    value.textContent = after;
    card.append(title, value, detail);
    cardContainer.appendChild(card);
  });
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
    renderTopology(state.topology, "baseline", "baseline");
    const metrics = await request("/api/metrics");
    if (metrics.status === "available") {
      renderMetrics(metrics.metrics);
      renderTopology(state.topology, metrics.metrics.plan_id, "applied");
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
elements.clearBatchButton.addEventListener("click", clearEnvelopeBatch);
elements.compileButton.addEventListener("click", compilePolicy);
elements.applyButton.addEventListener("click", applyPolicy);
document.querySelector("#reset-policy").addEventListener("click", resetPolicy);
initialize();
