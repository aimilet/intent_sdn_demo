// 页面交互逻辑：拉取后端 trace，播放决策步骤，渲染拓扑、属性和指标。
"use strict";

const scenarioButtons = [...document.querySelectorAll(".scenario-button")];
const componentList = document.querySelector("#componentList");
const propertyTable = document.querySelector("#propertyTable");
const summaryMetrics = document.querySelector("#summaryMetrics");
const candidateRows = document.querySelector("#candidateRows");
const executionPanel = document.querySelector("#executionPanel");
const linkLayer = document.querySelector("#linkLayer");
const nodeLayer = document.querySelector("#nodeLayer");
const vehicleMarker = document.querySelector("#vehicleMarker");
const stepRail = document.querySelector("#stepRail");
const stepTitle = document.querySelector("#stepTitle");
const stepIndex = document.querySelector("#stepIndex");
const stepSummary = document.querySelector("#stepSummary");
const stepDetails = document.querySelector("#stepDetails");
const stepMetrics = document.querySelector("#stepMetrics");
const scenarioTitle = document.querySelector("#scenarioTitle");
const scenarioFocus = document.querySelector("#scenarioFocus");
const caseCount = document.querySelector("#caseCount");
const memoryHits = document.querySelector("#memoryHits");
const selectedTarget = document.querySelector("#selectedTarget");
const playToggle = document.querySelector("#playToggle");
const prevStep = document.querySelector("#prevStep");
const nextStep = document.querySelector("#nextStep");
const rerun = document.querySelector("#rerun");
const resetMemory = document.querySelector("#resetMemory");
const workspace = document.querySelector(".workspace");

let currentScenario = "emergency";
let trace = null;
let currentStep = 0;
let selectedNodeId = "vehicle";
let timer = null;

scenarioButtons.forEach((button) => {
  button.addEventListener("click", () => {
    currentScenario = button.dataset.scenario;
    loadTrace();
  });
});

playToggle.addEventListener("click", () => {
  if (timer) {
    stopPlayback();
  } else {
    startPlayback();
  }
});

prevStep.addEventListener("click", () => {
  if (trace) {
    setStep(Math.max(0, currentStep - 1));
  }
});
nextStep.addEventListener("click", () => {
  if (trace) {
    setStep(Math.min(trace.steps.length - 1, currentStep + 1));
  }
});
rerun.addEventListener("click", () => loadTrace());
resetMemory.addEventListener("click", async () => {
  await fetch("/api/reset-history");
  await loadTrace();
});

loadTrace();

async function loadTrace() {
  workspace.classList.add("loading");
  stopPlayback();
  setActiveScenario();
  const response = await fetch(`/api/run?scenario=${encodeURIComponent(currentScenario)}`);
  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`);
  }
  trace = await response.json();
  currentStep = 0;
  selectedNodeId = "vehicle";
  renderAll();
  workspace.classList.remove("loading");
}

function renderAll() {
  scenarioTitle.textContent = trace.scenario.title;
  scenarioFocus.textContent = trace.scenario.focus;
  caseCount.textContent = `案例 ${trace.summary.caseCount}`;
  memoryHits.textContent = `命中 ${trace.summary.memoryHits}`;
  selectedTarget.textContent = trace.selected.target;
  renderSummaryMetrics();
  renderComponents();
  renderLinks();
  renderNodes();
  renderStepRail();
  renderCandidates();
  renderExecution();
  setStep(0);
}

function setActiveScenario() {
  scenarioButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.scenario === currentScenario);
  });
}

function renderSummaryMetrics() {
  const metrics = [
    ["同步质量", percent(trace.summary.syncQuality)],
    ["预测置信", percent(trace.summary.predictionConfidence)],
    ["历史命中", String(trace.summary.memoryHits)],
    ["主导意图", trace.summary.dominantIntent],
  ];
  summaryMetrics.innerHTML = metrics
    .map(
      ([label, value]) => `
        <div class="metric-tile">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderComponents() {
  componentList.innerHTML = trace.nodes
    .map(
      (node) => `
        <button class="component-item" data-node="${node.id}">
          <span class="component-icon type-${node.type}">${node.label.slice(0, 1)}</span>
          <span>
            <strong>${escapeHtml(node.label)}</strong>
            <span>${escapeHtml(node.subtitle)}</span>
          </span>
        </button>
      `,
    )
    .join("");

  componentList.querySelectorAll(".component-item").forEach((button) => {
    button.addEventListener("click", () => {
      selectedNodeId = button.dataset.node;
      renderNodeSelection();
      renderProperties();
    });
  });
}

function renderLinks() {
  linkLayer.innerHTML = trace.links
    .map((link) => {
      const from = nodeById(link.from);
      const to = nodeById(link.to);
      const mx = (from.x + to.x) / 2;
      const my = (from.y + to.y) / 2;
      return `
        <line id="link-${link.id}" class="flow-link" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}"></line>
        <text class="link-label" x="${mx}" y="${my - 1}">${escapeHtml(link.label)}</text>
      `;
    })
    .join("");
}

function renderNodes() {
  nodeLayer.innerHTML = trace.nodes
    .map(
      (node) => `
        <button class="map-node" data-node="${node.id}" style="left:${node.x}%; top:${node.y}%;">
          <span class="node-head">
            <span class="node-icon type-${node.type}">${node.label.slice(0, 1)}</span>
            <span>
              <strong>${escapeHtml(node.label)}</strong>
              <span>${escapeHtml(node.subtitle)}</span>
            </span>
          </span>
        </button>
      `,
    )
    .join("");

  nodeLayer.querySelectorAll(".map-node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", () => {
      selectedNodeId = nodeEl.dataset.node;
      renderNodeSelection();
      renderProperties();
    });
  });
}

function renderStepRail() {
  stepRail.innerHTML = trace.steps
    .map(
      (step, index) => `
        <button class="step-dot" data-step="${index}" title="${escapeHtml(step.title)}">${index + 1}</button>
      `,
    )
    .join("");
  stepRail.querySelectorAll(".step-dot").forEach((button) => {
    button.addEventListener("click", () => setStep(Number(button.dataset.step)));
  });
}

function renderCandidates() {
  candidateRows.innerHTML = trace.evaluations
    .slice()
    .sort((a, b) => b.satisfaction - a.satisfaction)
    .map((item) => {
      const classes = [item.selected ? "selected" : "", item.violation ? "violation" : ""]
        .filter(Boolean)
        .join(" ");
      return `
        <tr class="${classes}">
          <td>${escapeHtml(item.target)}${item.selected ? " *" : ""}</td>
          <td>${item.latencyMs.toFixed(1)}</td>
          <td>${item.energyJ.toFixed(2)}</td>
          <td>${item.reliability.toFixed(3)}</td>
          <td>${item.satisfaction.toFixed(3)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderExecution() {
  const items = [
    ["实际时延", `${trace.execution.latencyMs.toFixed(1)} ms`],
    ["实际能耗", `${trace.execution.energyJ.toFixed(2)} J`],
    ["可靠性", trace.execution.reliability.toFixed(3)],
    ["预测误差", percent(trace.execution.predictionError)],
  ];
  executionPanel.innerHTML = items
    .map(
      ([label, value]) => `
        <div class="execution-item">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `,
    )
    .join("");
}

function setStep(index) {
  if (!trace) {
    return;
  }
  currentStep = index;
  const step = trace.steps[currentStep];
  stepTitle.textContent = step.title;
  stepIndex.textContent = `${currentStep + 1} / ${trace.steps.length}`;
  stepSummary.textContent = step.summary;
  renderStepDetails(step);
  renderStepMetrics(step);
  renderActiveTopology(step);
  moveVehicle();
  stepRail.querySelectorAll(".step-dot").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.step) === currentStep);
  });
}

function renderStepDetails(step) {
  stepDetails.innerHTML = step.details
    .map((detail) => `<div class="detail-item">${escapeHtml(detail)}</div>`)
    .join("");
}

function renderStepMetrics(step) {
  const entries = Object.entries(step.metrics || {});
  stepMetrics.innerHTML = entries
    .map(([label, rawValue]) => {
      const value = Math.max(0, Math.min(100, Number(rawValue)));
      return `
        <div class="bar-row">
          <span>${escapeHtml(label)}</span>
          <span class="bar-track"><span class="bar-value" style="width:${value}%"></span></span>
          <strong>${Math.round(value)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderActiveTopology(step) {
  const activeNodes = new Set(step.activeNodes);
  const activeLinks = new Set(step.activeLinks);

  nodeLayer.querySelectorAll(".map-node").forEach((nodeEl) => {
    nodeEl.classList.toggle("active", activeNodes.has(nodeEl.dataset.node));
  });

  linkLayer.querySelectorAll(".flow-link").forEach((line) => {
    const id = line.id.replace("link-", "");
    line.classList.toggle("active", activeLinks.has(id));
    line.classList.toggle("selected", trace.selected && activeLinks.has(id));
  });

  if (!activeNodes.has(selectedNodeId) && activeNodes.size > 0) {
    selectedNodeId = [...activeNodes][0];
  }
  renderNodeSelection();
  renderProperties();
}

function renderNodeSelection() {
  document.querySelectorAll("[data-node]").forEach((item) => {
    item.classList.toggle("selected", item.dataset.node === selectedNodeId);
  });
}

function renderProperties() {
  const node = nodeById(selectedNodeId);
  const rows = Object.entries(node.attrs || {});
  propertyTable.innerHTML = rows
    .map(
      ([key, value]) => `
        <div class="property-row">
          <span>${escapeHtml(key)}</span>
          <strong>${escapeHtml(String(value))}</strong>
        </div>
      `,
    )
    .join("");
}

function moveVehicle() {
  const pathIndex = Math.min(currentStep, trace.vehiclePath.length - 1);
  const position = trace.vehiclePath[pathIndex];
  vehicleMarker.style.left = `${position.x}%`;
  vehicleMarker.style.top = `${position.y}%`;
}

function startPlayback() {
  if (!trace) {
    return;
  }
  playToggle.textContent = "Ⅱ";
  timer = window.setInterval(() => {
    if (currentStep >= trace.steps.length - 1) {
      setStep(0);
    } else {
      setStep(currentStep + 1);
    }
  }, 1350);
}

function stopPlayback() {
  playToggle.textContent = "▶";
  if (timer) {
    window.clearInterval(timer);
    timer = null;
  }
}

function nodeById(id) {
  return trace.nodes.find((node) => node.id === id);
}

function percent(value) {
  return `${Math.round(Number(value) * 100)}%`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
