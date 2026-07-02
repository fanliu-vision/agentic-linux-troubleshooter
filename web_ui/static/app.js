"use strict";

const state = {
  auth: null,
  projects: [],
  projectId: "",
  overview: null,
  runtime: null,
  worker: null,
  reports: [],
  jobs: [],
  events: [],
  selectedFingerprint: "",
  filter: "",
  loadingApproval: false,
  loadingRuntimeAction: "",
  loadingOperation: "",
  recoveryHistory: [],
  rollbackTarget: null,
  loadingHistoryRollback: "",
  detailTab: "evidence",
  reportCenter: null,
  selectedReportId: "",
  loadingReport: "",
  loadingLogin: false,
  selectedJobId: "",
  selectedJobLog: null,
  loadingJobAction: "",
  pollTimer: null,
  pendingConfirmation: null,
};

const els = {
  authPanel: document.getElementById("authPanel"),
  appShell: document.getElementById("appShell"),
  loginForm: document.getElementById("loginForm"),
  operatorInput: document.getElementById("operatorInput"),
  tokenInput: document.getElementById("tokenInput"),
  loginButton: document.getElementById("loginButton"),
  loginError: document.getElementById("loginError"),
  operatorBadge: document.getElementById("operatorBadge"),
  workerBadge: document.getElementById("workerBadge"),
  logoutButton: document.getElementById("logoutButton"),
  projectSelect: document.getElementById("projectSelect"),
  projectMeta: document.getElementById("projectMeta"),
  refreshButton: document.getElementById("refreshButton"),
  metricEvents: document.getElementById("metricEvents"),
  metricPending: document.getElementById("metricPending"),
  metricRecovered: document.getElementById("metricRecovered"),
  metricBlocked: document.getElementById("metricBlocked"),
  consoleSummary: document.getElementById("consoleSummary"),
  consoleConnectionStatus: document.getElementById("consoleConnectionStatus"),
  consoleMonitorStatus: document.getElementById("consoleMonitorStatus"),
  consoleWorkerStatus: document.getElementById("consoleWorkerStatus"),
  consoleLatestEvents: document.getElementById("consoleLatestEvents"),
  consolePendingApprovals: document.getElementById("consolePendingApprovals"),
  consoleRecentJobs: document.getElementById("consoleRecentJobs"),
  consoleRecentReports: document.getElementById("consoleRecentReports"),
  consoleConnectButton: document.getElementById("consoleConnectButton"),
  consoleStartMonitorButton: document.getElementById("consoleStartMonitorButton"),
  consoleGenerateReportButton: document.getElementById("consoleGenerateReportButton"),
  connectLocalButton: document.getElementById("connectLocalButton"),
  connectRemoteButton: document.getElementById("connectRemoteButton"),
  healthCheckButton: document.getElementById("healthCheckButton"),
  runtimeTarget: document.getElementById("runtimeTarget"),
  connectionStatus: document.getElementById("connectionStatus"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  checksSummary: document.getElementById("checksSummary"),
  healthChecks: document.getElementById("healthChecks"),
  jobsSummary: document.getElementById("jobsSummary"),
  jobList: document.getElementById("jobList"),
  operationStatus: document.getElementById("operationStatus"),
  opStartMonitor: document.getElementById("opStartMonitor"),
  opStopMonitor: document.getElementById("opStopMonitor"),
  opRefreshLogs: document.getElementById("opRefreshLogs"),
  opGenerateReport: document.getElementById("opGenerateReport"),
  opDryRunRecovery: document.getElementById("opDryRunRecovery"),
  opLiveApply: document.getElementById("opLiveApply"),
  opRollbackLatest: document.getElementById("opRollbackLatest"),
  jobLogPanel: document.getElementById("jobLogPanel"),
  jobLogTitle: document.getElementById("jobLogTitle"),
  jobLogMeta: document.getElementById("jobLogMeta"),
  jobLogContent: document.getElementById("jobLogContent"),
  jobLogRefreshButton: document.getElementById("jobLogRefreshButton"),
  historySummary: document.getElementById("historySummary"),
  refreshHistoryButton: document.getElementById("refreshHistoryButton"),
  recoveryHistoryList: document.getElementById("recoveryHistoryList"),
  recoveryHistoryEmpty: document.getElementById("recoveryHistoryEmpty"),
  eventFilter: document.getElementById("eventFilter"),
  eventRows: document.getElementById("eventRows"),
  emptyState: document.getElementById("emptyState"),
  detailContent: document.getElementById("detailContent"),
  detailTitle: document.getElementById("detailTitle"),
  detailSubtitle: document.getElementById("detailSubtitle"),
  detailStatus: document.getElementById("detailStatus"),
  evidenceList: document.getElementById("evidenceList"),
  approvalPanel: document.getElementById("approvalPanel"),
  plannedEdits: document.getElementById("plannedEdits"),
  policyJson: document.getElementById("policyJson"),
  rollbackJson: document.getElementById("rollbackJson"),
  traceList: document.getElementById("traceList"),
  auditJson: document.getElementById("auditJson"),
  detailTabEvidence: document.getElementById("detailTabEvidence"),
  detailTabPolicy: document.getElementById("detailTabPolicy"),
  detailTabPlan: document.getElementById("detailTabPlan"),
  detailTabApproval: document.getElementById("detailTabApproval"),
  detailTabExecution: document.getElementById("detailTabExecution"),
  detailTabReports: document.getElementById("detailTabReports"),
  detailTabAudit: document.getElementById("detailTabAudit"),
  detailPaneEvidence: document.getElementById("detailPaneEvidence"),
  detailPanePolicy: document.getElementById("detailPanePolicy"),
  detailPanePlan: document.getElementById("detailPanePlan"),
  detailPaneApproval: document.getElementById("detailPaneApproval"),
  detailPaneExecution: document.getElementById("detailPaneExecution"),
  detailPaneReports: document.getElementById("detailPaneReports"),
  detailPaneAudit: document.getElementById("detailPaneAudit"),
  refreshReportsButton: document.getElementById("refreshReportsButton"),
  reportGroups: document.getElementById("reportGroups"),
  reportEmpty: document.getElementById("reportEmpty"),
  reportDetailBox: document.getElementById("reportDetailBox"),
  reportTitle: document.getElementById("reportTitle"),
  reportMeta: document.getElementById("reportMeta"),
  reportType: document.getElementById("reportType"),
  reportContent: document.getElementById("reportContent"),
  confirmOverlay: document.getElementById("confirmOverlay"),
  confirmTitle: document.getElementById("confirmTitle"),
  confirmSubtitle: document.getElementById("confirmSubtitle"),
  confirmRiskBadge: document.getElementById("confirmRiskBadge"),
  confirmActionLabel: document.getElementById("confirmActionLabel"),
  confirmProject: document.getElementById("confirmProject"),
  confirmOperator: document.getElementById("confirmOperator"),
  confirmRequestId: document.getElementById("confirmRequestId"),
  confirmImpact: document.getElementById("confirmImpact"),
  confirmWordLabel: document.getElementById("confirmWordLabel"),
  confirmWordInput: document.getElementById("confirmWordInput"),
  confirmWarning: document.getElementById("confirmWarning"),
  confirmCancelButton: document.getElementById("confirmCancelButton"),
  confirmSubmitButton: document.getElementById("confirmSubmitButton"),
};

const UI_LABELS = {
  approved: "已批准",
  approved_recovery_job: "审批后执行",
  approval_approve: "审批通过",
  approval_expire: "审批过期",
  approval_reject: "审批拒绝",
  approval_rejected: "审批已拒绝",
  blocked: "已阻断",
  connected: "已连接",
  connecting: "连接中",
  detected: "已检测",
  diagnostic_report: "诊断报告",
  disconnected: "未连接",
  dry_run_recovery: "执行 dry-run 恢复",
  audit_json: "审计 JSON",
  auto_recovery_report: "自动恢复报告",
  event_report: "事件报告",
  execution_blocked: "执行已阻断",
  execution_failed: "执行失败",
  execution_finished: "执行完成",
  execution_started: "开始执行",
  execution_succeeded: "执行成功",
  expired: "已过期",
  failed: "失败",
  generate_report: "生成报告",
  health_check: "健康检查",
  high: "高",
  cancel_requested: "取消中",
  canceled: "已取消",
  timed_out: "已超时",
  job_retry: "重试高风险任务",
  live_apply: "执行 live apply",
  local: "本地",
  low: "低",
  manual_escalation: "人工升级",
  medium: "中",
  monitor_running: "监控运行中",
  pending: "待审批",
  pending_approval: "待审批",
  policy_decided: "策略已决策",
  precheck_completed: "预检完成",
  queued: "排队中",
  recovered: "已恢复",
  rejected: "已拒绝",
  remote: "远程",
  report_only: "仅报告",
  rollback_done: "已回滚",
  rollback_failed: "回滚失败",
  rollback_finished: "回滚完成",
  rollback_started: "开始回滚",
  rollback_report: "回滚报告",
  rollback_latest: "回滚最近一次修复",
  available: "可回滚",
  auth_required: "需要登录",
  confirmation_required: "需要二次确认",
  fix_applied: "已修复",
  not_available: "不可用",
  running: "运行中",
  service_running: "服务运行中",
  start_monitor: "启动监控",
  stop_monitor: "停止监控",
  succeeded: "成功",
  missing: "文件不存在",
  ok: "可读",
  path_not_allowed: "路径不允许",
  truncated: "内容已截断",
};

const HIGH_RISK_ACTIONS = {
  live_apply: {
    label: "执行 live apply",
    confirmWord: "LIVE APPLY",
    impact: "会消费已批准的恢复请求，并在后台重新经过安全 gate 后执行真实 apply。",
  },
  rollback_latest: {
    label: "回滚最近一次修复",
    confirmWord: "ROLLBACK",
    impact: "会回滚当前项目最近一次可回滚修复，并写入 rollback trace、job 和审计报告。",
  },
  recovery_history_rollback: {
    label: "回滚这条修复历史",
    confirmWord: "ROLLBACK",
    impact: "会针对所选恢复历史创建回滚任务；只有当前最新可回滚记录会被执行。",
  },
  approval_approve: {
    label: "批准并进入审批后执行流程",
    confirmWord: "APPROVE",
    impact: "会把审批请求标记为 approved，并自动排队 approved_recovery_job。",
  },
  job_retry: {
    label: "重试高风险任务",
    confirmWord: "RETRY",
    impact: "会复制原高风险任务参数重新排队；worker 仍会执行安全检查。",
  },
};

const ERROR_EXPLANATIONS = {
  auth_required: "登录已过期或尚未登录，请重新登录。",
  csrf_required: "页面安全 token 已失效，请刷新页面后重试。",
  confirmation_required: "该操作需要二次确认。",
  invalid_token: "访问 token 不正确。请使用启动 UI 时设置的 AGENTIC_TRACE_UI_TOKEN 值。",
  job_not_terminal: "只有已结束的任务才能重试。",
  permission_denied: "当前角色没有执行该操作的权限。",
  unsupported_operation: "该操作不在 UI 受控 allowlist 中。",
};

const OPERATION_PERMISSIONS = {
  start_monitor: "operate",
  stop_monitor: "operate",
  refresh_logs: "operate",
  generate_report: "operate",
  dry_run_recovery: "operate",
  live_apply: "live_apply",
  rollback_latest: "rollback",
};
const CONNECTION_PERMISSION = "operate";
const APPROVAL_PERMISSION = "approve";
const ROLLBACK_PERMISSION = "rollback";
const HIGH_RISK_RETRY_PERMISSION = "retry_high_risk";
const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "cancel_requested"]);
const RETRYABLE_JOB_STATUSES = new Set(["failed", "blocked", "canceled", "timed_out"]);
const HIGH_RISK_JOB_ACTIONS = new Set(["live_apply", "rollback_latest", "approved_recovery_job"]);
const ATTENTION_EVENT_STATUSES = new Set(["blocked", "failed", "execution_blocked", "execution_failed"]);
const COMPLETE_EVENT_STATUSES = new Set(["recovered", "fix_applied", "execution_succeeded", "rollback_done"]);
const HIGH_ATTENTION_SEVERITIES = new Set(["critical", "high"]);
const REPORT_PREVIEW_LIMIT = 12000;
const DETAIL_TABS = [
  ["evidence", "detailTabEvidence", "detailPaneEvidence"],
  ["policy", "detailTabPolicy", "detailPanePolicy"],
  ["plan", "detailTabPlan", "detailPanePlan"],
  ["approval", "detailTabApproval", "detailPaneApproval"],
  ["execution", "detailTabExecution", "detailPaneExecution"],
  ["reports", "detailTabReports", "detailPaneReports"],
  ["audit", "detailTabAudit", "detailPaneAudit"],
];

function text(value, fallback = "") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : fallback;
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function boolText(value) {
  return value ? "是" : "否";
}

function display(value, fallback = "未知") {
  const raw = text(value, "");
  if (!raw) {
    return fallback;
  }
  return UI_LABELS[raw] || raw;
}

function friendlyError(message) {
  return ERROR_EXPLANATIONS[message] || display(message, message);
}

function pretty(value) {
  return JSON.stringify(value || {}, null, 2);
}

function cssToken(value) {
  return text(value, "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") {
      node.className = value;
    } else if (key === "text") {
      node.textContent = value;
    } else if (key.startsWith("data-")) {
      node.setAttribute(key, value);
    } else if (key === "title") {
      node.title = value;
    } else if (key === "type") {
      node.type = value;
    } else if (key === "disabled") {
      node.disabled = Boolean(value);
    } else if (key === "placeholder") {
      node.placeholder = value;
    } else if (key === "ariaLabel") {
      node.setAttribute("aria-label", value);
    }
  });
  children.forEach((child) => {
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && method !== "HEAD" && state.auth?.csrf_token) {
    headers["X-CSRF-Token"] = state.auth.csrf_token;
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    headers,
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    state.auth = body.auth || { authenticated: false, auth_required: true };
    renderAuth();
  }
  if (!response.ok) {
    const message = body.message || body.error || response.statusText;
    throw new Error(friendlyError(message));
  }
  return body;
}

function renderAuth() {
  const authenticated = Boolean(state.auth?.authenticated);
  els.authPanel.classList.toggle("hidden", authenticated);
  els.appShell.classList.toggle("hidden", !authenticated);
  els.operatorBadge.textContent = authenticated
    ? `操作员：${text(state.auth.operator, "operator")} · ${display(state.auth.role, state.auth.role || "viewer")}`
    : "未登录";
  els.loginButton.disabled = state.loadingLogin;
  els.loginButton.textContent = state.loadingLogin ? "登录中" : "登录";
}

function hasPermission(permission) {
  return new Set(state.auth?.permissions || []).has(permission);
}

function canRunOperation(action) {
  return hasPermission(OPERATION_PERMISSIONS[action] || "operate");
}

function canUseConnectionActions() {
  return hasPermission(CONNECTION_PERMISSION);
}

function showLoginError(message) {
  els.loginError.textContent = display(message, message);
  els.loginError.classList.toggle("hidden", !message);
}

function confirmationProjectLabel() {
  const project = selectedProject();
  if (!project.project_id) {
    return state.projectId || "-";
  }
  return project.name
    ? `${project.name} (${project.project_id})`
    : project.project_id;
}

function confirmationTarget(context = {}) {
  return [
    context.requestId ? `request=${context.requestId}` : "",
    context.jobId ? `job=${context.jobId}` : "",
    context.rollbackIdentity ? `rollback=${context.rollbackIdentity}` : "",
    context.fingerprint ? `event=${context.fingerprint}` : "",
  ].filter(Boolean).join(" | ") || "-";
}

function closeHighRiskDialog(result) {
  if (!state.pendingConfirmation) {
    return;
  }
  const pending = state.pendingConfirmation;
  state.pendingConfirmation = null;
  els.confirmOverlay.classList.add("hidden");
  els.confirmWarning.textContent = "";
  els.confirmWordInput.value = "";
  pending.resolve(result);
}

function highRiskConfirmation(action, context = {}) {
  const config = HIGH_RISK_ACTIONS[action];
  if (!config) {
    return Promise.resolve({});
  }

  if (state.pendingConfirmation) {
    closeHighRiskDialog(null);
  }

  const expected = config.confirmWord;
  els.confirmTitle.textContent = "确认高风险操作";
  els.confirmSubtitle.textContent = "该操作会记录操作员、job、trace 和审计信息；后台 worker 会继续执行安全检查。";
  els.confirmRiskBadge.textContent = context.risk || "高风险";
  els.confirmActionLabel.textContent = config.label;
  els.confirmProject.textContent = confirmationProjectLabel();
  els.confirmOperator.textContent = text(state.auth?.operator, "operator");
  els.confirmRequestId.textContent = confirmationTarget(context);
  els.confirmImpact.textContent = context.impact || config.impact;
  els.confirmWordLabel.textContent = `输入确认词：${expected}`;
  els.confirmWarning.textContent = "";
  els.confirmSubmitButton.disabled = true;
  els.confirmSubmitButton.classList.add("primary-danger");
  els.confirmOverlay.classList.remove("hidden");
  els.confirmWordInput.value = "";
  els.confirmWordInput.focus();

  return new Promise((resolve) => {
    state.pendingConfirmation = {
      action,
      expected,
      resolve,
    };
  });
}

function updateConfirmationSubmitState() {
  const pending = state.pendingConfirmation;
  if (!pending) {
    return;
  }
  const matches = els.confirmWordInput.value.trim() === pending.expected;
  els.confirmSubmitButton.disabled = !matches;
  els.confirmWarning.textContent = matches ? "" : `请输入 ${pending.expected} 后继续`;
}

function submitHighRiskDialog() {
  const pending = state.pendingConfirmation;
  if (!pending) {
    return;
  }
  if (els.confirmWordInput.value.trim() !== pending.expected) {
    updateConfirmationSubmitState();
    return;
  }
  closeHighRiskDialog({
    confirm: true,
    confirmation_action: pending.action,
  });
}

function selectedProject() {
  return state.projects.find((project) => project.project_id === state.projectId) || {};
}

function setProjectMeta() {
  const project = selectedProject();
  if (!project.project_id) {
    els.projectMeta.textContent = "未配置项目";
    return;
  }
  const approval = project.require_human_approval_for_live_apply
    ? "live apply 需要人工审批"
    : "live apply 可按策略直接执行";
  els.projectMeta.textContent = `${project.name || project.project_id} - ${display(project.mode, "模式未知")} - ${approval}`;
}

function renderProjects() {
  els.projectSelect.innerHTML = "";
  state.projects.forEach((project) => {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = project.name
      ? `${project.name} (${project.project_id})`
      : project.project_id;
    els.projectSelect.appendChild(option);
  });
  els.projectSelect.value = state.projectId;
  setProjectMeta();
}

function renderOverview() {
  const overview = state.overview || {};
  els.metricEvents.textContent = text(overview.events_total, "0");
  els.metricPending.textContent = text(overview.pending_approvals, "0");
  els.metricRecovered.textContent = text(overview.recovered, "0");
  els.metricBlocked.textContent = text(overview.blocked, "0");
  renderConsole();
}

function renderConsole() {
  const runtime = state.runtime || {};
  const worker = state.worker || {};
  const workerRunning = Boolean(worker.running);
  const latestEvents = workflowEvents(state.events || []).slice(0, 4);
  const pending = (state.events || []).filter((event) => event.pending_approval).slice(0, 4);
  const recentJobs = (state.jobs || []).slice(0, 4);
  const recentReports = (state.reports || []).slice(0, 4);

  els.consoleSummary.textContent = [
    selectedProject().name || state.projectId || "未选择项目",
    runtime.target || runtime.connection_mode || "未连接",
  ].filter(Boolean).join(" | ");

  els.consoleConnectionStatus.className = statusClass(runtime.connection_status || "disconnected");
  els.consoleConnectionStatus.textContent = display(runtime.connection_status, "未连接");
  els.consoleMonitorStatus.className = statusClass(runtime.runtime_status || "disconnected");
  els.consoleMonitorStatus.textContent = display(runtime.runtime_status, "未运行");
  els.consoleWorkerStatus.className = statusClass(workerRunning ? "connected" : "error");
  els.consoleWorkerStatus.textContent = workerRunning
    ? `运行中 - ${text(worker.worker_id, "worker")}`
    : "未运行";
  els.workerBadge.className = `worker-badge ${workerRunning ? "worker-ok" : "worker-error"}`;
  els.workerBadge.textContent = workerRunning ? "worker 运行中" : "worker 未运行";

  renderConsoleItems(els.consoleLatestEvents, latestEvents, consoleEventItem, "暂无待处理事件，可先启动监控");
  renderConsoleItems(els.consolePendingApprovals, pending, consoleEventItem, "暂无待审批");
  renderConsoleItems(els.consoleRecentJobs, recentJobs, consoleJobItem, "暂无任务，可先生成报告");
  renderConsoleItems(els.consoleRecentReports, recentReports, consoleReportItem, "暂无报告，可先生成首次报告");
}

function renderConsoleItems(target, items, itemRenderer, emptyText) {
  target.innerHTML = "";
  if (!items.length) {
    target.appendChild(el("p", { class: "muted", text: emptyText }));
    return;
  }
  items.forEach((item) => target.appendChild(itemRenderer(item)));
}

function consoleEventItem(event) {
  const button = el("button", {
    type: "button",
    class: "console-row-button",
    text: text(event.event_type, "未知事件"),
  });
  button.addEventListener("click", () => selectEvent(event.fingerprint));
  return el("div", { class: "console-row" }, [
    el("span", { class: `status-pill status-${cssToken(event.status)}`, text: display(event.status, "未知") }),
    el("div", { class: "console-row-main" }, [
      button,
      el("span", { text: text(event.summary || event.fingerprint, "-") }),
    ]),
  ]);
}

function consoleJobItem(job) {
  const button = el("button", {
    type: "button",
    class: "console-row-button",
    text: display(job.action, "任务"),
  });
  button.addEventListener("click", () => openJobLog(job.job_id));
  return el("div", { class: "console-row" }, [
    el("span", { class: `job-status status-${cssToken(job.status)}`, text: display(job.status, "未知") }),
    el("div", { class: "console-row-main" }, [
      button,
      el("span", { text: text(job.summary, "-") }),
    ]),
  ]);
}

function consoleReportItem(report) {
  const button = el("button", {
    type: "button",
    class: "console-row-button",
    text: text(report.title, "报告"),
  });
  button.addEventListener("click", () => openReportFromConsole(report.report_id));
  return el("div", { class: "console-row" }, [
    el("span", { class: `tag report-${cssToken(report.report_type)}`, text: display(report.report_type, "报告") }),
    el("div", { class: "console-row-main" }, [
      button,
      el("span", { text: [report.generated_at, report.job_id ? `job=${report.job_id}` : ""].filter(Boolean).join(" | ") }),
    ]),
  ]);
}

function statusClass(status) {
  return `status-text status-${cssToken(status)}`;
}

function renderRuntime() {
  const runtime = state.runtime || {};
  const checks = runtime.checks || [];
  const jobs = state.jobs || [];
  const target = runtime.target || runtime.connection_mode || "";

  els.connectionStatus.className = statusClass(runtime.connection_status || "disconnected");
  els.connectionStatus.textContent = display(runtime.connection_status, "未连接");
  els.runtimeStatus.className = statusClass(runtime.runtime_status || "disconnected");
  els.runtimeStatus.textContent = display(runtime.runtime_status, "未连接");
  els.runtimeTarget.textContent = target
    ? `${display(runtime.connection_mode, "连接")} - ${target}`
    : "尚未连接";
  els.checksSummary.textContent = `${checks.length} 项`;
  els.jobsSummary.textContent = `${jobs.length} 个`;

  const busy = Boolean(state.loadingRuntimeAction);
  const canConnect = canUseConnectionActions();
  els.connectLocalButton.disabled = busy || !canConnect;
  els.connectRemoteButton.disabled = busy || !canConnect;
  els.healthCheckButton.disabled = busy || !canConnect;
  els.consoleConnectButton.disabled = busy || !canConnect;
  els.connectLocalButton.textContent = state.loadingRuntimeAction === "local"
    ? "连接中"
    : "连接本地";
  els.connectRemoteButton.textContent = state.loadingRuntimeAction === "remote"
    ? "连接中"
    : "连接远程";
  els.healthCheckButton.textContent = state.loadingRuntimeAction === "health"
    ? "检查中"
    : "健康检查";
  els.consoleConnectButton.textContent = state.loadingRuntimeAction
    ? "连接中"
    : "连接项目";

  renderChecks(checks);
  renderJobs(jobs);
  renderOperationButtons();
  renderConsole();
}

function renderOperationButtons() {
  const buttons = [
    [els.opStartMonitor, "start_monitor", "启动监控"],
    [els.opStopMonitor, "stop_monitor", "停止监控"],
    [els.opRefreshLogs, "refresh_logs", "刷新日志"],
    [els.opGenerateReport, "generate_report", "生成报告"],
    [els.opDryRunRecovery, "dry_run_recovery", "执行 dry-run 恢复"],
    [els.opLiveApply, "live_apply", "执行 live apply"],
    [els.opRollbackLatest, "rollback_latest", "回滚最近一次修复"],
  ];
  buttons.forEach(([button, action, label]) => {
    button.disabled = Boolean(state.loadingOperation) || !canRunOperation(action);
    button.textContent = state.loadingOperation === action ? "提交中" : label;
  });
  els.consoleStartMonitorButton.disabled = Boolean(state.loadingOperation) || !canRunOperation("start_monitor");
  els.consoleGenerateReportButton.disabled = Boolean(state.loadingOperation) || !canRunOperation("generate_report");
}

function renderRecoveryHistory() {
  const records = state.recoveryHistory || [];
  const available = records.filter((item) => item.rollback_available).length;
  els.historySummary.textContent = records.length
    ? `${records.length} 条修复记录，${available} 条当前可回滚`
    : "展示已执行修复、字段变更、备份与回滚状态";
  els.recoveryHistoryList.innerHTML = "";
  els.recoveryHistoryEmpty.classList.toggle("hidden", records.length > 0);

  records.slice(0, 8).forEach((record) => {
    els.recoveryHistoryList.appendChild(recoveryHistoryItem(record));
  });
}

function recoveryHistoryItem(record) {
  const edits = record.edits || [];
  const backup = record.backup_record || {};
  const status = record.rollback_status || (record.rollback_available ? "available" : "not_available");
  const button = el("button", {
    type: "button",
    class: record.rollback_available ? "danger" : "",
    text: state.loadingHistoryRollback === record.identity ? "回滚中" : "回滚",
    disabled: !record.rollback_available || Boolean(state.loadingHistoryRollback) || !hasPermission(ROLLBACK_PERMISSION),
    title: record.rollback_available ? "回滚这条最新修复" : "只有最新未回滚记录可回滚",
  });
  button.addEventListener("click", () => rollbackHistory(record.identity));

  const headerMeta = [
    display(record.mode, "模式未知"),
    record.event_type,
    record.fingerprint,
    record.job_id ? `job=${record.job_id}` : "",
  ].filter(Boolean).join(" | ");

  return el("article", { class: "history-item" }, [
    el("div", { class: "history-item-head" }, [
      el("div", { class: "history-title" }, [
        el("strong", { text: text(record.fix_id, "未知修复") }),
        el("span", { text: headerMeta || text(record.created_at, "-") }),
      ]),
      el("div", { class: "history-actions" }, [
        el("span", {
          class: `status-pill status-${cssToken(status)}`,
          text: display(status, "未知"),
        }),
        button,
      ]),
    ]),
    historyEditsTable(edits, backup),
    el("div", { class: "history-backup" }, [
      summaryItem("rollback", `${display(status, "未知")}${record.rollback_job_id ? ` | job=${record.rollback_job_id}` : ""}`),
      summaryItem("apply record", backup.record_path || record.record_path || "-"),
      summaryItem("backup", text(backup.backup_paths || [], "-")),
      summaryItem("diff", text(backup.diff_paths || [], "-")),
    ]),
  ]);
}

function editArtifact(edit, backup) {
  const values = [
    edit.diff_path ? `diff: ${edit.diff_path}` : "",
    edit.backup_path ? `backup: ${edit.backup_path}` : "",
    edit.record_path ? `record: ${edit.record_path}` : "",
  ].filter(Boolean);
  if (values.length) {
    return values.join(" | ");
  }
  const backupDiffs = text(backup?.diff_paths || [], "");
  const backupPaths = text(backup?.backup_paths || [], "");
  return [backupDiffs ? `diff: ${backupDiffs}` : "", backupPaths ? `backup: ${backupPaths}` : ""]
    .filter(Boolean)
    .join(" | ") || "-";
}

function historyEditsTable(edits, backup = {}) {
  const table = el("table", { class: "history-edits-table" });
  const thead = el("thead", {}, [
    el("tr", {}, [
      el("th", { text: "字段" }),
      el("th", { text: "变更" }),
      el("th", { text: "配置" }),
      el("th", { text: "diff / backup" }),
    ]),
  ]);
  const tbody = el("tbody");
  if (!edits.length) {
    const td = el("td", { class: "muted", text: "暂无字段变更，可查看下方 rollback / diff 状态" });
    td.colSpan = 4;
    tbody.appendChild(el("tr", {}, [td]));
  } else {
    edits.forEach((edit) => {
      tbody.appendChild(el("tr", {}, [
        el("td", { text: editField(edit) }),
        el("td", { class: "history-change", text: `${editOldValue(edit)} -> ${editNewValue(edit)}` }),
        el("td", { text: text(edit.config_path, "-") }),
        el("td", { class: "history-artifact", text: editArtifact(edit, backup) }),
      ]));
    });
  }
  table.appendChild(thead);
  table.appendChild(tbody);
  return el("div", { class: "history-edits-wrap" }, [table]);
}

function renderChecks(checks) {
  els.healthChecks.innerHTML = "";
  if (!checks.length) {
    els.healthChecks.appendChild(el("li", { class: "muted", text: "暂无检查结果" }));
    return;
  }

  checks.forEach((check) => {
    els.healthChecks.appendChild(el("li", { class: "check-item" }, [
      el("span", {
        class: `check-status check-${cssToken(check.status)}`,
        text: display(check.status, "未知"),
      }),
      el("div", { class: "check-body" }, [
        el("strong", { text: text(check.name, "check") }),
        el("span", { text: text(check.message, "-") }),
      ]),
    ]));
  });
}

function renderJobs(jobs) {
  els.jobList.innerHTML = "";
  if (!jobs.length) {
    els.jobList.appendChild(el("li", { class: "muted", text: "暂无任务" }));
    renderJobLog();
    return;
  }

  jobs.slice(0, 6).forEach((job) => {
    const result = job.result || {};
    const details = [
      result.output_summary,
      result.failure_reason ? `失败原因: ${result.failure_reason}` : "",
      result.related_trace ? `trace: ${text(result.related_trace)}` : "",
    ].filter(Boolean).join(" | ");
    const meta = [
      `attempt ${text(job.attempt, "0")}/${text(job.max_attempts, "1")}`,
      job.operator ? `operator=${job.operator}` : "",
      job.lease_owner ? `lease=${job.lease_owner}` : "",
      job.timeout_seconds ? `timeout=${job.timeout_seconds}s` : "",
    ].filter(Boolean).join(" | ");
    const logButton = el("button", {
      type: "button",
      text: state.loadingJobAction === `log:${job.job_id}` ? "打开中" : "日志",
      disabled: state.loadingJobAction === `log:${job.job_id}`,
    });
    logButton.addEventListener("click", () => openJobLog(job.job_id));

    const cancelButton = el("button", {
      type: "button",
      text: state.loadingJobAction === `cancel:${job.job_id}` ? "取消中" : "取消",
      disabled: !ACTIVE_JOB_STATUSES.has(job.status)
        || Boolean(state.loadingJobAction)
        || !hasPermission(HIGH_RISK_JOB_ACTIONS.has(job.action) ? HIGH_RISK_RETRY_PERMISSION : "operate"),
    });
    cancelButton.addEventListener("click", () => cancelJob(job.job_id));

    const retryButton = el("button", {
      type: "button",
      text: state.loadingJobAction === `retry:${job.job_id}` ? "重试中" : "重试",
      disabled: !RETRYABLE_JOB_STATUSES.has(job.status)
        || Boolean(state.loadingJobAction)
        || !hasPermission(HIGH_RISK_JOB_ACTIONS.has(job.action) ? HIGH_RISK_RETRY_PERMISSION : "operate"),
    });
    if (HIGH_RISK_JOB_ACTIONS.has(job.action)) {
      retryButton.classList.add("danger");
    }
    retryButton.addEventListener("click", () => retryJob(job));

    els.jobList.appendChild(el("li", { class: "job-item" }, [
      el("span", {
        class: `job-status status-${cssToken(job.status)}`,
        text: display(job.status, "未知"),
      }),
      el("div", { class: "job-body" }, [
        el("strong", { text: display(job.action, "任务") }),
        el("span", { text: `${text(job.summary, "-")} - ${text(job.updated_at, "-")}` }),
        el("span", { text: meta }),
        el("span", { text: details || "无输出摘要" }),
      ]),
      el("div", { class: "job-actions" }, [
        logButton,
        cancelButton,
        retryButton,
      ]),
    ]));
  });
  renderJobLog();
}

function renderJobLog() {
  const log = state.selectedJobLog || {};
  if (!state.selectedJobId) {
    els.jobLogPanel.classList.add("hidden");
    return;
  }
  const job = selectedJob();
  const autoRefresh = ACTIVE_JOB_STATUSES.has(job.status)
    ? "自动刷新中"
    : "自动刷新已停止";
  els.jobLogPanel.classList.remove("hidden");
  els.jobLogTitle.textContent = `${display(job.action, "任务")}日志`;
  els.jobLogMeta.textContent = [
    state.selectedJobId,
    display(job.status, job.status),
    autoRefresh,
    job.log_path || log.log_path,
    log.updated_at ? `log=${log.updated_at}` : "",
  ].filter(Boolean).join(" | ");
  els.jobLogContent.textContent = log.text || "暂无日志";
}

function selectedJob() {
  return state.jobs.find((item) => item.job_id === state.selectedJobId) || {};
}

async function openJobLog(jobId) {
  if (!state.projectId || !jobId) {
    return;
  }
  state.selectedJobId = jobId;
  state.loadingJobAction = `log:${jobId}`;
  renderJobs(state.jobs);
  try {
    const log = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(jobId)}/log`,
    );
    state.selectedJobLog = log;
    renderJobLog();
  } catch (error) {
    els.operationStatus.textContent = error.message;
  } finally {
    state.loadingJobAction = "";
    renderJobs(state.jobs);
  }
}

async function cancelJob(jobId) {
  if (!state.projectId || !jobId || state.loadingJobAction) {
    return;
  }
  state.loadingJobAction = `cancel:${jobId}`;
  renderJobs(state.jobs);
  try {
    const result = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(jobId)}/cancel`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    );
    state.jobs = result.jobs || state.jobs;
    const job = result.job || {};
    els.operationStatus.textContent = `${display(job.action, "任务")}: ${display(job.status, "未知")} - ${text(job.summary, "")}`;
    await loadRuntimeAndJobs();
  } catch (error) {
    els.operationStatus.textContent = error.message;
  } finally {
    state.loadingJobAction = "";
    renderJobs(state.jobs);
  }
}

async function retryJob(job) {
  if (!state.projectId || !job?.job_id || state.loadingJobAction) {
    return;
  }
  const body = {};
  if (HIGH_RISK_JOB_ACTIONS.has(job.action)) {
    const confirmation = await highRiskConfirmation("job_retry", {
      jobId: job.job_id,
      requestId: job.payload?.request_id || job.result?.request_id || "",
      impact: `重试 ${display(job.action, "高风险任务")}，原任务状态为 ${display(job.status, "未知")}。`,
    });
    if (confirmation === null) {
      return;
    }
    Object.assign(body, confirmation);
  }
  state.loadingJobAction = `retry:${job.job_id}`;
  renderJobs(state.jobs);
  try {
    const result = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(job.job_id)}/retry`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    state.jobs = result.jobs || state.jobs;
    const queued = result.job || {};
    state.selectedJobId = queued.job_id || state.selectedJobId;
    state.selectedJobLog = null;
    els.operationStatus.textContent = `${display(queued.action, "任务")}: ${display(queued.status, "未知")} - ${text(queued.summary, "")}`;
    await loadRuntimeAndJobs();
  } catch (error) {
    els.operationStatus.textContent = error.message;
  } finally {
    state.loadingJobAction = "";
    renderJobs(state.jobs);
  }
}

function eventTimestamp(event) {
  const value = Date.parse(
    event.updated_at || event.detected_at || event.created_at || event.timestamp || "",
  );
  return Number.isNaN(value) ? 0 : value;
}

function eventWorkflowRank(event) {
  const approval = text(event.approval_status, "");
  const status = text(event.status, "");
  const severity = text(event.severity, "");
  if (event.pending_approval || approval === "pending" || approval === "pending_approval") {
    return 0;
  }
  if (approval === "approved") {
    return 1;
  }
  if (ATTENTION_EVENT_STATUSES.has(status)) {
    return 2;
  }
  if (HIGH_ATTENTION_SEVERITIES.has(severity)) {
    return 3;
  }
  if (status === "manual_escalation") {
    return 4;
  }
  if (COMPLETE_EVENT_STATUSES.has(status)) {
    return 7;
  }
  return 5;
}

function workflowEvents(events) {
  return [...(events || [])].sort((left, right) => {
    const rank = eventWorkflowRank(left) - eventWorkflowRank(right);
    if (rank !== 0) {
      return rank;
    }
    return eventTimestamp(right) - eventTimestamp(left);
  });
}

function defaultEventFingerprint() {
  return workflowEvents(state.events)[0]?.fingerprint || "";
}

function filteredEvents() {
  const query = state.filter.trim().toLowerCase();
  const rows = workflowEvents(state.events);
  if (!query) {
    return rows;
  }
  return rows.filter((event) => {
    const haystack = [
      event.status,
      event.severity,
      event.event_type,
      event.action,
      event.approval_status,
      event.fingerprint,
      event.summary,
      event.source,
    ].map((value) => text(value).toLowerCase()).join(" ");
    return haystack.includes(query);
  });
}

function statusPill(status) {
  return el("span", {
    class: `status-pill status-${cssToken(status)}`,
    text: display(status, "未知"),
  });
}

function severityTag(severity) {
  return el("span", {
    class: `tag severity-${cssToken(severity)}`,
    text: display(severity, "未知"),
  });
}

function renderEvents() {
  els.eventRows.innerHTML = "";
  const rows = filteredEvents();

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const emptyText = state.filter.trim()
      ? "没有匹配事件，清空筛选或刷新项目"
      : "暂无事件，可先启动监控或生成首次报告";
    const td = el("td", { text: emptyText, class: "muted" });
    td.colSpan = 6;
    tr.appendChild(td);
    els.eventRows.appendChild(tr);
    return;
  }

  rows.forEach((event) => {
    const tr = document.createElement("tr");
    if (event.fingerprint === state.selectedFingerprint) {
      tr.classList.add("selected");
    }
    tr.addEventListener("click", () => selectEvent(event.fingerprint));

    const typeCell = el("td", {}, [
      el("div", { class: "event-main" }, [
        el("span", { class: "event-type", text: text(event.event_type, "未知事件") }),
        el("span", { class: "event-fingerprint", text: text(event.fingerprint) }),
      ]),
    ]);

    tr.appendChild(el("td", {}, [statusPill(event.status)]));
    tr.appendChild(el("td", {}, [severityTag(event.severity)]));
    tr.appendChild(typeCell);
    tr.appendChild(el("td", { text: display(event.action, "未知") }));
    tr.appendChild(el("td", { text: boolText(event.dry_run) }));
    tr.appendChild(el("td", { text: event.pending_approval ? "待审批" : display(event.approval_status, "-") }));
    els.eventRows.appendChild(tr);
  });
}

function renderKvList(target, items) {
  target.innerHTML = "";
  items.forEach(([label, value]) => {
    target.appendChild(el("dt", { text: label }));
    target.appendChild(el("dd", { text: text(value, "-") }));
  });
}

function editField(edit) {
  return text(edit.field_path || edit.field || edit.path || edit.key || edit.setting || edit.name, "字段");
}

function editOldValue(edit) {
  return text(edit.old_value ?? edit.old ?? edit.before ?? edit.current_value, "-");
}

function editNewValue(edit) {
  return text(edit.new_value ?? edit.new ?? edit.after ?? edit.desired_value, "-");
}

function renderPlannedEdits(edits) {
  els.plannedEdits.innerHTML = "";
  if (!edits.length) {
    const tr = document.createElement("tr");
    const td = el("td", { text: "暂无计划变更", class: "muted" });
    td.colSpan = 5;
    tr.appendChild(td);
    els.plannedEdits.appendChild(tr);
    return;
  }

  edits.forEach((edit) => {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", { text: editField(edit) }));
    tr.appendChild(el("td", { text: editOldValue(edit) }));
    tr.appendChild(el("td", { text: editNewValue(edit) }));
    tr.appendChild(el("td", { text: text(edit.semantic_rule || edit.rule || edit.reason || edit.source, "-") }));
    tr.appendChild(el("td", { text: text(edit.semantic_status || edit.status || edit.safety || edit.kind, "-") }));
    els.plannedEdits.appendChild(tr);
  });
}

function renderApprovalPanel(detail) {
  const approval = detail.approval || {};
  const request = approval.request || {};
  const latest = approval.latest || {};
  const edits = detail.planned_edits || [];
  const rollback = detail.rollback_plan || {};
  const rollbackAvailable = request.rollback_available ?? rollback.available ?? rollback.rollback_available;

  els.approvalPanel.innerHTML = "";

  if (!request.request_id && !latest.request_id) {
    els.approvalPanel.appendChild(el("p", { class: "muted", text: "暂无审批请求" }));
    return;
  }

  els.approvalPanel.appendChild(el("div", { class: "approval-summary" }, [
    summaryItem("状态", display(latest.status || request.status, "-")),
    summaryItem("请求", request.request_id || latest.request_id || "-"),
    summaryItem("修复", request.selected_fix_id || latest.selected_fix_id || "-"),
    summaryItem("可回滚", boolText(Boolean(rollbackAvailable))),
    summaryItem("范围", request.approval_scope || latest.approval_scope || "-"),
    summaryItem("安全原因", request.safety_reason || latest.decision_reason || "-"),
  ]));

  const editBox = el("div", { class: "approval-edits" });
  if (edits.length) {
    edits.forEach((edit) => {
      editBox.appendChild(el("div", { class: "approval-edit" }, [
        el("div", {}, [
          el("span", { text: "字段" }),
          el("strong", { text: editField(edit) }),
        ]),
        el("div", {}, [
          el("span", { text: "变更" }),
          el("strong", { text: `${editOldValue(edit)} -> ${editNewValue(edit)}` }),
        ]),
      ]));
    });
  } else {
    editBox.appendChild(el("p", { class: "muted", text: "暂无可执行计划变更" }));
  }
  els.approvalPanel.appendChild(editBox);

  if (approval.pending) {
    const textarea = el("textarea", {
      placeholder: "拒绝原因",
      ariaLabel: "拒绝原因",
    });
    const approveButton = el("button", {
      class: "primary",
      text: state.loadingApproval ? "批准中" : "批准",
      type: "button",
      disabled: state.loadingApproval || !hasPermission(APPROVAL_PERMISSION),
    });
    const rejectButton = el("button", {
      class: "danger",
      text: state.loadingApproval ? "拒绝中" : "拒绝",
      type: "button",
      disabled: state.loadingApproval || !hasPermission(APPROVAL_PERMISSION),
    });

    approveButton.addEventListener("click", () => submitApproval(request.request_id, "approve", ""));
    rejectButton.addEventListener("click", () => submitApproval(request.request_id, "reject", textarea.value));

    els.approvalPanel.appendChild(el("div", { class: "approval-actions" }, [
      approveButton,
      rejectButton,
      textarea,
    ]));
  }
}

function summaryItem(label, value) {
  return el("div", {}, [
    el("span", { text: label }),
    el("strong", { text: text(value, "-") }),
  ]);
}

function renderTrace(trace) {
  els.traceList.innerHTML = "";
  if (!trace.length) {
    els.traceList.appendChild(el("li", { class: "muted", text: "暂无 trace 事件" }));
    return;
  }

  trace.forEach((item) => {
    const payload = item.payload || {};
    const bits = [
      item.event_type,
      payload.status,
      payload.selected_fix_id,
      payload.decision_reason,
      payload.reason,
    ].filter(Boolean).join(" - ");

    els.traceList.appendChild(el("li", { class: "trace-item" }, [
      el("div", { class: "trace-time", text: text(item.created_at, "-") }),
      el("div", { class: "trace-body" }, [
        el("div", { class: "trace-stage", text: display(item.stage, "未知阶段") }),
        el("div", { class: "trace-meta", text: bits || text(item.summary, "-") }),
      ]),
    ]));
  });
}

function setDetailTab(tab) {
  state.detailTab = DETAIL_TABS.some(([name]) => name === tab) ? tab : "evidence";
  renderDetailTabs();
}

function renderDetailTabs() {
  DETAIL_TABS.forEach(([name, buttonKey, paneKey]) => {
    const active = state.detailTab === name;
    els[buttonKey].classList.toggle("active", active);
    els[paneKey].classList.toggle("hidden", !active);
  });
}

function reportGroups(center) {
  return [
    ["latest", "最新诊断报告", center.latest || []],
    ["event", "事件关联报告", center.event || []],
    ["auto_recovery", "自动恢复报告", center.auto_recovery || []],
    ["rollback", "回滚报告", center.rollback || []],
    ["audit_json", "审计 JSON", center.audit_json || []],
  ];
}

function renderReportCenter(center) {
  state.reportCenter = center || {};
  els.reportGroups.innerHTML = "";
  if (!state.selectedReportId) {
    els.reportDetailBox.classList.add("hidden");
  }
  let total = 0;

  reportGroups(state.reportCenter).forEach(([, label, reports]) => {
    if (!reports.length) {
      return;
    }
    total += reports.length;
    const list = el("ul", { class: "report-list" });
    reports.forEach((report) => {
      list.appendChild(reportListItem(report));
    });
    els.reportGroups.appendChild(el("section", { class: "report-group" }, [
      el("h4", { text: `${label} (${reports.length})` }),
      list,
    ]));
  });

  els.reportEmpty.classList.toggle("hidden", total > 0);
  if (total === 0) {
    state.selectedReportId = "";
  }
}

function reportListItem(report) {
  const button = el("button", {
    type: "button",
    text: state.loadingReport === report.report_id ? "打开中" : "打开",
    disabled: state.loadingReport === report.report_id,
  });
  button.addEventListener("click", () => openReport(report.report_id));

  const meta = [
    display(report.report_type, "报告"),
    report.generated_at,
    report.event_type,
    report.job_id ? `job=${report.job_id}` : "",
  ].filter(Boolean).join(" | ");

  return el("li", { class: "report-item" }, [
    el("div", { class: "report-main" }, [
      el("strong", { text: text(report.title, "报告") }),
      el("span", { text: meta || text(report.path, "-") }),
      el("span", { text: text(report.path, "-") }),
    ]),
    button,
  ]);
}

function reportLineCount(content) {
  if (!content) {
    return 0;
  }
  return content.split(/\r?\n/).length;
}

function reportPreviewHeading(content) {
  const lines = text(content, "").split(/\r?\n/);
  const heading = lines.find((line) => /^#{1,3}\s+\S/.test(line.trim()));
  if (heading) {
    return heading.trim().replace(/^#{1,3}\s+/, "").trim();
  }
  return lines.find((line) => line.trim() && !/^-{3,}$/.test(line.trim()))?.trim() || "";
}

function reportPreviewText(content, status) {
  if (!content) {
    return `无法读取报告内容：${display(status, "未知状态")}`;
  }
  const preview = content.length > REPORT_PREVIEW_LIMIT
    ? `${content.slice(0, REPORT_PREVIEW_LIMIT)}\n\n[内容已截断，打开报告文件查看完整内容]`
    : content;
  const heading = reportPreviewHeading(content);
  return heading && !/^#{1,3}\s+\S/.test(preview.trimStart())
    ? `# ${heading}\n\n${preview}`
    : preview;
}

function renderReportDetail(detail) {
  const report = detail.report || {};
  const status = detail.content_status || "";
  const content = detail.content || "";
  const lineCount = reportLineCount(content);
  const charCount = content.length;
  els.reportDetailBox.classList.remove("hidden");
  els.reportTitle.textContent = text(report.title, "报告详情");
  els.reportType.className = `tag report-${cssToken(report.report_type)}`;
  els.reportType.textContent = display(report.report_type, "报告");
  els.reportMeta.textContent = [
    report.generated_at,
    report.event_type,
    report.fingerprint,
    report.job_id ? `job=${report.job_id}` : "",
    display(status, status),
    lineCount ? `${lineCount} 行` : "",
    charCount ? `${charCount} 字符` : "",
    charCount > REPORT_PREVIEW_LIMIT ? "预览已截断" : "",
  ].filter(Boolean).join(" | ");
  els.reportContent.textContent = reportPreviewText(content, status);
}

async function openReport(reportId) {
  if (!reportId || state.loadingReport) {
    return;
  }
  state.loadingReport = reportId;
  renderReportCenter(state.reportCenter || {});
  try {
    const detail = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/reports/${encodeURIComponent(reportId)}`,
    );
    state.selectedReportId = reportId;
    renderReportDetail(detail);
  } catch (error) {
    renderError(error.message);
  } finally {
    state.loadingReport = "";
    renderReportCenter(state.reportCenter || {});
  }
}

async function openReportFromConsole(reportId) {
  if (!reportId) {
    return;
  }
  els.emptyState.classList.add("hidden");
  els.detailContent.classList.remove("hidden");
  els.detailTitle.textContent = "报告中心";
  els.detailSubtitle.textContent = "最近生成的诊断、恢复、回滚和审计报告";
  els.detailStatus.className = "status-pill status-succeeded";
  els.detailStatus.textContent = "报告";
  renderKvList(els.evidenceList, []);
  renderApprovalPanel({});
  renderPlannedEdits([]);
  els.policyJson.textContent = pretty({});
  els.rollbackJson.textContent = pretty({});
  renderTrace([]);
  els.auditJson.textContent = pretty({});
  renderReportCenter({
    latest: state.reports || [],
    event: [],
    auto_recovery: [],
    rollback: [],
    audit_json: [],
  });
  setDetailTab("reports");
  await openReport(reportId);
}

function renderDetail(detail) {
  const summary = detail.summary || {};
  const evidence = detail.evidence || {};

  els.emptyState.classList.add("hidden");
  els.detailContent.classList.remove("hidden");
  els.detailTitle.textContent = text(summary.event_type, "未知事件");
  els.detailSubtitle.textContent = `${text(summary.fingerprint, "-")} - ${text(summary.summary, "暂无摘要")}`;
  els.detailStatus.className = `status-pill status-${cssToken(summary.status)}`;
  els.detailStatus.textContent = display(summary.status, "未知");

  renderKvList(els.evidenceList, [
    ["摘要", evidence.summary],
    ["来源", evidence.source],
    ["签名", evidence.signature],
    ["匹配关键词", evidence.matched_keywords],
    ["原始片段", boolText(evidence.raw_excerpt_present)],
  ]);

  renderApprovalPanel(detail);
  renderPlannedEdits(detail.planned_edits || []);
  els.policyJson.textContent = pretty(detail.policy_decision);
  els.rollbackJson.textContent = pretty(detail.rollback_plan);
  renderTrace(detail.trace || []);
  els.auditJson.textContent = pretty(detail.audit_json);
  state.selectedReportId = "";
  renderReportCenter(detail.report_center || {});
  renderDetailTabs();
}

function clearDetail(message, options = {}) {
  els.emptyState.innerHTML = "";
  if (options.guide) {
    els.emptyState.appendChild(el("div", { class: "empty-guide" }, [
      el("h3", { text: message }),
      el("p", { text: "从连接项目开始；如果已有日志，可启动监控或生成首次报告建立运行基线。" }),
      el("div", { class: "empty-guide-actions" }, [
        guidedActionButton("连接项目", () => runRuntimeAction(selectedProject().mode === "local" ? "local" : "remote")),
        guidedActionButton("启动监控", () => runOperation("start_monitor")),
        guidedActionButton("生成首次报告", () => runOperation("generate_report")),
      ]),
    ]));
  } else {
    els.emptyState.textContent = message;
  }
  els.emptyState.classList.remove("hidden");
  els.detailContent.classList.add("hidden");
  state.reportCenter = null;
  state.selectedReportId = "";
}

function guidedActionButton(label, handler) {
  const button = el("button", { type: "button", text: label });
  button.addEventListener("click", handler);
  return button;
}

function renderError(message) {
  const box = el("div", { class: "error-box", text: message });
  els.detailContent.prepend(box);
  window.setTimeout(() => box.remove(), 4500);
}

async function selectEvent(fingerprint) {
  state.selectedFingerprint = fingerprint;
  renderEvents();
  if (!fingerprint) {
    clearDetail("未选择事件");
    return;
  }

  try {
    const detail = await api(`/api/projects/${encodeURIComponent(state.projectId)}/events/${encodeURIComponent(fingerprint)}`);
    renderDetail(detail);
  } catch (error) {
    clearDetail(error.message);
  }
}

async function submitApproval(requestId, action, comment) {
  if (!requestId || state.loadingApproval) {
    return;
  }
  const confirmation = await highRiskConfirmation(`approval_${action}`, {
    requestId,
    fingerprint: state.selectedFingerprint,
    impact: action === "approve"
      ? "批准后会立即排队 approved_recovery_job；恢复 worker 会重新校验 fix_id、fingerprint、rollback 和 runtime gate。"
      : "该审批决策会写入 trace 和 approval log。",
  });
  if (confirmation === null) {
    return;
  }
  state.loadingApproval = true;
  try {
    await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/approvals/${encodeURIComponent(requestId)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({ comment: comment || "", ...(confirmation || {}) }),
      },
    );
    await loadProject({ keepSelection: true });
  } catch (error) {
    renderError(error.message);
  } finally {
    state.loadingApproval = false;
  }
}

async function runRuntimeAction(kind) {
  if (!state.projectId || state.loadingRuntimeAction) {
    return;
  }

  const projectPart = encodeURIComponent(state.projectId);
  const action = kind === "health" ? "health-check" : "connect";
  const body = {};
  if (kind === "local" || kind === "remote") {
    body.connection_mode = kind;
  }

  state.loadingRuntimeAction = kind;
  renderRuntime();
  try {
    const result = await api(`/api/projects/${projectPart}/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.runtime = result.runtime || state.runtime;
    await loadRuntimeAndJobs();
  } catch (error) {
    els.runtimeTarget.textContent = error.message;
  } finally {
    state.loadingRuntimeAction = "";
    renderRuntime();
  }
}

async function runOperation(action) {
  if (!state.projectId || state.loadingOperation) {
    return;
  }

  const projectPart = encodeURIComponent(state.projectId);
  const confirmation = await highRiskConfirmation(action, {
    requestId: action === "live_apply"
      ? text((state.events || []).find((event) => event.approval_status === "approved")?.request_id, "")
      : "",
    impact: action === "rollback_latest"
      ? "会尝试回滚当前项目最近一次可回滚修复；执行前后都会写入 job、trace、报告和恢复历史。"
      : undefined,
  });
  if (confirmation === null) {
    return;
  }
  state.loadingOperation = action;
  els.operationStatus.textContent = "任务正在入队";
  renderOperationButtons();
  try {
    const result = await api(`/api/projects/${projectPart}/operations/${encodeURIComponent(action)}`, {
      method: "POST",
      body: JSON.stringify({ ...(confirmation || {}) }),
    });
    state.jobs = result.jobs || state.jobs;
    const job = result.job || {};
    els.operationStatus.textContent = `${display(job.action, action)}: ${display(job.status, "未知")} - ${text(job.summary, "")}`;
    await loadRuntimeAndJobs();
  } catch (error) {
    els.operationStatus.textContent = error.message;
  } finally {
    state.loadingOperation = "";
    renderOperationButtons();
  }
}

async function rollbackHistory(identity) {
  if (!state.projectId || !identity || state.loadingHistoryRollback) {
    return;
  }
  const projectPart = encodeURIComponent(state.projectId);
  const target = (state.recoveryHistory || []).find((item) => item.identity === identity) || {};
  const confirmation = await highRiskConfirmation("recovery_history_rollback", {
    requestId: target.request_id || "",
    jobId: target.job_id || "",
    rollbackIdentity: identity,
    fingerprint: target.fingerprint || "",
    impact: `将回滚 fix=${text(target.fix_id, "unknown")} 的字段变更；只有最新可回滚记录会被后端接受。`,
  });
  if (confirmation === null) {
    return;
  }
  state.loadingHistoryRollback = identity;
  renderRecoveryHistory();
  try {
    const result = await api(
      `/api/projects/${projectPart}/recovery-history/${encodeURIComponent(identity)}/rollback`,
      {
        method: "POST",
        body: JSON.stringify({ ...confirmation }),
      },
    );
    state.jobs = result.jobs || state.jobs;
    const job = result.job || {};
    els.operationStatus.textContent = `${display(job.action, "回滚")}: ${display(job.status, "未知")} - ${text(job.summary, "")}`;
    await loadProject({ keepSelection: true });
  } catch (error) {
    renderError(error.message);
  } finally {
    state.loadingHistoryRollback = "";
    renderRecoveryHistory();
  }
}

async function loadRuntimeAndJobs() {
  if (!state.projectId) {
    state.runtime = null;
    state.worker = null;
    state.jobs = [];
    renderRuntime();
    return;
  }

  const projectPart = encodeURIComponent(state.projectId);
  const [runtime, jobsData, workerData, reportsData] = await Promise.all([
    api(`/api/projects/${projectPart}/runtime`),
    api(`/api/projects/${projectPart}/jobs`),
    api(`/api/projects/${projectPart}/worker`),
    api(`/api/projects/${projectPart}/reports`),
  ]);
  state.runtime = runtime;
  state.jobs = jobsData.jobs || [];
  state.worker = workerData.worker || {};
  state.reports = reportsData.reports || state.reports;
  if (state.selectedJobId && !state.jobs.some((job) => job.job_id === state.selectedJobId)) {
    state.selectedJobId = "";
    state.selectedJobLog = null;
  }
  renderRuntime();
  if (state.selectedJobId && !state.loadingJobAction) {
    await refreshSelectedJobLog();
  }
}

async function refreshSelectedJobLog() {
  if (!state.projectId || !state.selectedJobId) {
    return;
  }
  try {
    state.selectedJobLog = await api(
      `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(state.selectedJobId)}/log`,
    );
    renderJobLog();
  } catch (error) {
    state.selectedJobLog = { text: error.message };
    renderJobLog();
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    if (!state.auth?.authenticated || !state.projectId) {
      return;
    }
    try {
      await loadRuntimeAndJobs();
    } catch (error) {
      // Polling should not steal focus from the active workflow.
    }
  }, 3000);
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function loadRecoveryHistory() {
  if (!state.projectId) {
    state.recoveryHistory = [];
    state.rollbackTarget = null;
    renderRecoveryHistory();
    return;
  }
  const data = await api(`/api/projects/${encodeURIComponent(state.projectId)}/recovery-history`);
  state.recoveryHistory = data.records || [];
  state.rollbackTarget = data.rollback_target || null;
  renderRecoveryHistory();
}

async function loadProject({ keepSelection = false } = {}) {
  if (!state.projectId) {
    state.reports = [];
    state.worker = null;
    renderOverview();
    renderRuntime();
    renderRecoveryHistory();
    renderEvents();
    clearDetail("未选择项目");
    return;
  }

  const projectPart = encodeURIComponent(state.projectId);
  const [overview, eventData, runtime, jobsData, recoveryHistory, reportsData, workerData] = await Promise.all([
    api(`/api/projects/${projectPart}/overview`),
    api(`/api/projects/${projectPart}/events`),
    api(`/api/projects/${projectPart}/runtime`),
    api(`/api/projects/${projectPart}/jobs`),
    api(`/api/projects/${projectPart}/recovery-history`),
    api(`/api/projects/${projectPart}/reports`),
    api(`/api/projects/${projectPart}/worker`),
  ]);
  state.overview = overview;
  state.events = eventData.events || [];
  state.runtime = runtime;
  state.jobs = jobsData.jobs || [];
  state.reports = reportsData.reports || [];
  state.worker = workerData.worker || {};
  state.recoveryHistory = recoveryHistory.records || [];
  state.rollbackTarget = recoveryHistory.rollback_target || null;

  if (!keepSelection || !state.events.some((event) => event.fingerprint === state.selectedFingerprint)) {
    state.selectedFingerprint = defaultEventFingerprint();
  }

  renderOverview();
  renderRuntime();
  renderRecoveryHistory();
  renderEvents();
  if (state.selectedFingerprint) {
    await selectEvent(state.selectedFingerprint);
  } else {
    clearDetail("当前尚未记录任何事件", { guide: true });
  }
}

async function loadAuthStatus() {
  const data = await api("/api/auth/status");
  state.auth = data.auth || { authenticated: false };
  renderAuth();
  return Boolean(state.auth.authenticated);
}

async function loadProjectsFromServer() {
  const projectData = await api("/api/projects");
  state.projects = projectData.projects || [];
  const params = new URLSearchParams(window.location.search);
  const requestedProject = params.get("project") || "";
  state.projectId = requestedProject
    || state.projectId
    || state.projects[0]?.project_id
    || "";
  if (state.projectId && !state.projects.some((project) => project.project_id === state.projectId)) {
    state.projectId = state.projects[0]?.project_id || "";
  }
  renderProjects();
}

async function login(event) {
  event.preventDefault();
  if (state.loadingLogin) {
    return;
  }
  state.loadingLogin = true;
  showLoginError("");
  renderAuth();
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        operator: els.operatorInput.value || "operator",
        token: els.tokenInput.value || "",
      }),
    });
    state.auth = data.auth || { authenticated: false };
    els.tokenInput.value = "";
    renderAuth();
    if (state.auth.authenticated) {
      await loadProjectsFromServer();
      await loadProject();
      startPolling();
    }
  } catch (error) {
    showLoginError(error.message);
  } finally {
    state.loadingLogin = false;
    renderAuth();
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    });
  } catch (error) {
    // The local state is still cleared if the server session already expired.
  }
  state.auth = { authenticated: false, auth_required: true };
  state.projects = [];
  state.projectId = "";
  state.events = [];
  state.jobs = [];
  state.reports = [];
  state.worker = null;
  state.recoveryHistory = [];
  state.selectedJobId = "";
  state.selectedJobLog = null;
  stopPolling();
  renderAuth();
  clearDetail("未登录");
}

async function init() {
  const authenticated = await loadAuthStatus();
  if (!authenticated) {
    stopPolling();
    clearDetail("未登录");
    return;
  }
  await loadProjectsFromServer();
  await loadProject();
  startPolling();
}

els.loginForm.addEventListener("submit", login);

els.logoutButton.addEventListener("click", logout);

els.projectSelect.addEventListener("change", async (event) => {
  state.projectId = event.target.value;
  state.selectedFingerprint = "";
  state.selectedJobId = "";
  state.selectedJobLog = null;
  setProjectMeta();
  await loadProject();
});

els.refreshButton.addEventListener("click", async () => {
  await loadProject({ keepSelection: true });
});

els.refreshHistoryButton.addEventListener("click", async () => {
  await loadRecoveryHistory();
});

els.jobLogRefreshButton.addEventListener("click", async () => {
  await refreshSelectedJobLog();
});

DETAIL_TABS.forEach(([name, buttonKey]) => {
  els[buttonKey].addEventListener("click", () => {
    setDetailTab(name);
  });
});

els.refreshReportsButton.addEventListener("click", async () => {
  if (!state.selectedFingerprint) {
    await loadRuntimeAndJobs();
    renderReportCenter({
      latest: state.reports || [],
      event: [],
      auto_recovery: [],
      rollback: [],
      audit_json: [],
    });
    return;
  }
  state.detailTab = "reports";
  await selectEvent(state.selectedFingerprint);
});

els.connectLocalButton.addEventListener("click", async () => {
  await runRuntimeAction("local");
});

els.connectRemoteButton.addEventListener("click", async () => {
  await runRuntimeAction("remote");
});

els.healthCheckButton.addEventListener("click", async () => {
  await runRuntimeAction("health");
});

els.consoleConnectButton.addEventListener("click", async () => {
  await runRuntimeAction(selectedProject().mode === "local" ? "local" : "remote");
});

els.consoleStartMonitorButton.addEventListener("click", async () => {
  await runOperation("start_monitor");
});

els.consoleGenerateReportButton.addEventListener("click", async () => {
  await runOperation("generate_report");
});

els.opStartMonitor.addEventListener("click", async () => {
  await runOperation("start_monitor");
});

els.opStopMonitor.addEventListener("click", async () => {
  await runOperation("stop_monitor");
});

els.opRefreshLogs.addEventListener("click", async () => {
  await runOperation("refresh_logs");
});

els.opGenerateReport.addEventListener("click", async () => {
  await runOperation("generate_report");
});

els.opDryRunRecovery.addEventListener("click", async () => {
  await runOperation("dry_run_recovery");
});

els.opLiveApply.addEventListener("click", async () => {
  await runOperation("live_apply");
});

els.opRollbackLatest.addEventListener("click", async () => {
  await runOperation("rollback_latest");
});

els.eventFilter.addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderEvents();
});

els.confirmWordInput.addEventListener("input", updateConfirmationSubmitState);

els.confirmWordInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    submitHighRiskDialog();
  }
});

els.confirmSubmitButton.addEventListener("click", submitHighRiskDialog);

els.confirmCancelButton.addEventListener("click", () => {
  closeHighRiskDialog(null);
});

els.confirmOverlay.addEventListener("click", (event) => {
  if (event.target === els.confirmOverlay) {
    closeHighRiskDialog(null);
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.pendingConfirmation) {
    closeHighRiskDialog(null);
  }
});

init().catch((error) => {
  clearDetail(error.message);
});
