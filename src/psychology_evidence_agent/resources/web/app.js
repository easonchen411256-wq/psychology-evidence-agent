const defaultQuestion =
  "在老年人跌倒恐惧相关的心理干预中，心率变异性（HRV）和步态指标如何被用于监测干预过程与评估效果？";
const defaultSearchQuery =
  "(older adults OR elderly) AND (fear of falling OR fall-related anxiety) AND (psychological intervention OR cognitive behavioral OR mindfulness) AND (heart rate OR HRV OR gait)";
const modelProviderPresets = {
  codex_cli: { label: "本机 Codex CLI", api_base: "", external: false },
  openai: { label: "OpenAI API", api_base: "https://api.openai.com/v1", external: true },
  deepseek: { label: "DeepSeek API", api_base: "https://api.deepseek.com", external: true },
  qwen: { label: "阿里云百炼 / Qwen", api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1", external: true },
  custom: { label: "其他 OpenAI-compatible", api_base: "", external: true },
};

const stages = [
  ["searching", "文献检索", "发现并整理候选题录"],
  ["screening", "相关性筛选", "区分直接、邻近和背景证据"],
  ["retrieving_fulltext", "全文获取", "只寻找合法公开版本"],
  ["extracting_evidence", "证据提取", "将研究结果放入证据卡"],
  ["synthesizing", "证据综合", "比较研究设计与结论边界"],
  ["drafting", "草稿生成", "形成可核查的研究草稿"],
];
const stageIndex = Object.fromEntries(stages.map((item, index) => [item[0], index]));
const statusLabels = {
  created: "待启动",
  running: "运行中",
  waiting_for_human: "等待人工处理",
  completed: "已完成",
  failed: "运行失败",
  cancelled: "已取消",
};
const decisionLabels = {
  include: "纳入",
  exclude: "排除",
  keep_uncertain: "保留为不确定",
  skip_paper: "跳过该文献",
  provide_fulltext: "提供全文",
};
const state = {
  view: location.hash.slice(1) || "overview",
  question: localStorage.getItem("pea.question") || defaultQuestion,
  runId: sessionStorage.getItem("pea.run_id") || "",
  runSnapshot: null,
  capabilities: null,
  intakeMessages: [],
  intakeBrief: null,
  intakeReady: false,
  intakeFallback: false,
  intakeBriefEditing: false,
  intakeLoading: false,
  intakeDraft: "",
  intakeScrollTop: 0,
  intakeFollow: true,
  searchMode: "automatic",
  searchPreferences: { mode: "automatic", manual_query: "", candidate_limit: 30 },
  modelConnection: {
    provider: "codex_cli",
    model: "",
    api_base: "",
    api_key: "",
    timeout_seconds: 120,
    json_mode: true,
  },
  modelConnectionReset: false,
  modelSettingsOpen: false,
  history: [],
  historyLoading: false,
  searchResults: [],
  searchResultsMeta: null,
  searchResultsLoading: false,
  screeningResults: [],
  screeningLoading: false,
  papers: [],
  selectedPaper: null,
  query: localStorage.getItem("pea.query") || defaultSearchQuery,
  yearFrom: 2015,
  maxResults: 12,
  loading: false,
  notice: null,
};
let pollTimer = null;

const app = document.querySelector("#app");
const saveQuestion = () => localStorage.setItem("pea.question", state.question);
const escapeHtml = (value = "") =>
  String(value).replace(/[&<>'"]/g, (character) =>
    ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    })[character]);
const formatDate = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const statusLabel = (value) => statusLabels[value] || value || "未知";
const stageLabel = (value) =>
  stages.find((item) => item[0] === value)?.[1] || (value === "initializing" ? "初始化" : value || "—");
const setNotice = (text, type = "info") => {
  state.notice = { text, type };
};
const noticeMarkup = () =>
  state.notice
    ? `<div class="notice ${state.notice.type}" role="status">${escapeHtml(state.notice.text)}</div>`
    : "";
const pageIntro = (eyebrow, title, subtitle) =>
  `<div class="page-intro"><span class="eyebrow">${eyebrow}</span><h1>${title}</h1><p>${subtitle}</p></div>${researchContextMarkup()}`;
const button = (label, id, extra = "", type = "button") =>
  `<button id="${id}" class="button ${extra}" type="${type}">${label}</button>`;

function api(path, options = {}) {
  const apiKey = sessionStorage.getItem("pea.api_key") || "";
  const headers = { ...(apiKey ? { "X-API-Key": apiKey } : {}), ...(options.headers || {}) };
  if (options.body instanceof FormData) delete headers["Content-Type"];
  else headers["Content-Type"] = "application/json";
  return fetch(path, { ...options, headers }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.detail || `请求失败（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return payload;
  });
}

function currentRun() {
  return state.runSnapshot?.run || null;
}

function persistIntakeState() {
  try {
    sessionStorage.setItem("pea.intake_state", JSON.stringify({
      messages: state.intakeMessages.slice(-40),
      brief: state.intakeBrief,
      ready: state.intakeReady,
      fallback: state.intakeFallback,
      draft: state.intakeDraft,
      searchMode: state.searchMode,
      searchPreferences: state.searchPreferences,
    }));
  } catch {
    // Private browsing or restricted storage should not prevent the workspace from running.
  }
}

function restoreIntakeState() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("pea.intake_state") || "null");
    if (!saved || !Array.isArray(saved.messages)) return;
    state.intakeMessages = saved.messages.filter((message) => message && ["user", "assistant"].includes(message.role) && typeof message.content === "string");
    state.intakeBrief = saved.brief && typeof saved.brief === "object" ? saved.brief : null;
    state.intakeReady = Boolean(saved.ready && state.intakeBrief);
    state.intakeFallback = Boolean(saved.fallback);
    state.intakeDraft = typeof saved.draft === "string" ? saved.draft : "";
    state.searchMode = saved.searchMode === "manual" ? "manual" : "automatic";
    if (saved.searchPreferences && typeof saved.searchPreferences === "object") {
      state.searchPreferences = { ...state.searchPreferences, ...saved.searchPreferences };
    }
    if (state.intakeBrief?.research_question) {
      state.question = state.intakeBrief.research_question;
      saveQuestion();
    }
  } catch {
    sessionStorage.removeItem("pea.intake_state");
  }
}

function researchContextMarkup() {
  if (state.view === "overview") return "";
  const run = currentRun();
  const brief = state.runSnapshot?.research_brief || state.intakeBrief || {};
  const question = run?.research_question || brief.research_question || "";
  if (!question) return "";
  const status = run ? `${statusLabel(run.status)} · ${stageLabel(run.stage)}` : "尚未启动 Agent";
  return `<div class="research-context-bar"><div><span class="eyebrow">CURRENT RESEARCH</span><strong>${escapeHtml(question)}</strong><small>${escapeHtml(status)}${run ? ` · ${escapeHtml(run.run_id)}` : ""}</small></div><a class="text-link" href="#overview">回到 Agent 总览 →</a></div>`;
}

function currentStageClass(stage) {
  const run = currentRun();
  if (!run) return "pending";
  const current = stageIndex[run.stage];
  const index = stageIndex[stage];
  if (run.status === "completed" || index < current) return "completed";
  if (run.status === "failed" && index === current) return "failed";
  if (index === current) return "current";
  return "pending";
}

function renderRunProgress(snapshot, run) {
  const progress = snapshot?.progress;
  if (!progress) return "";
  const completedStages = run.status === "completed"
    ? progress.stage_count
    : Math.max(0, progress.stage_index);
  const width = Math.min(100, Math.max(0, (completedStages / progress.stage_count) * 100));
  const unitText = progress.unit_total
    ? `已完成 ${progress.unit_completed || 0} / ${progress.unit_total} 个工作单元`
    : progress.current_step
      ? `正在执行：${progress.current_step}`
      : "正在处理当前阶段";
  return `<div class="run-progress" aria-label="Agent 执行进度"><div class="progress-track"><span style="width:${width}%"></span><i></i></div><div class="progress-meta"><span>阶段 ${Math.min(progress.stage_index + 1, progress.stage_count)} / ${progress.stage_count}</span><span>${escapeHtml(unitText)}</span></div></div>`;
}

function renderRunSummary() {
  const run = currentRun();
  if (!run) {
    return `<div class="run-summary empty-summary"><span class="status-dot idle"></span><div><small>当前运行</small><strong>还没有正在处理的研究任务</strong><p>启动后，Agent 会在这里持续显示阶段、计划和需要你处理的事项。</p></div></div>`;
  }
  const budget = state.runSnapshot.budget;
  const budgetText = budget ? `${budget.used_steps} / ${budget.max_steps} 步骤` : "预算初始化中";
  const snapshot = state.runSnapshot;
  const stopAction = snapshot.cancel_requested
    ? `<button class="button secondary danger" type="button" disabled>停止请求已记录</button>`
    : button("停止运行", "cancel-run", "secondary danger");
  const recoveryAction = snapshot.recovery_available
    ? button("从检查点恢复", "resume-run", "primary")
    : "";
  const runNote = snapshot.cancel_requested
    ? "Agent 会在当前安全步骤结束后停止。"
    : snapshot.recovery_available
      ? "服务重启或后台任务中断；可以从最近检查点恢复。"
      : `运行 ${escapeHtml(run.run_id)} · ${budgetText} · 更新于 ${escapeHtml(formatDate(run.updated_at))}`;
  return `<div class="run-summary"><span class="status-dot ${escapeHtml(run.status)}"></span><div><small>${escapeHtml(statusLabel(run.status))} · ${escapeHtml(stageLabel(run.stage))}</small><strong>${escapeHtml(run.research_question)}</strong><p>${runNote}</p>${renderRunProgress(snapshot, run)}</div><div class="summary-actions">${button("刷新", "refresh-run", "secondary")}${run.status === "waiting_for_human" ? button("查看待处理", "jump-review", "primary") : ""}${run.status === "running" && !snapshot.recovery_available ? stopAction : ""}${recoveryAction}${run.status === "failed" && run.failure?.retryable ? button("重试", "retry-run", "primary") : ""}</div></div>`;
}

function renderTimeline() {
  const title = currentRun() ? "Agent 正在怎样推进" : "Agent 将怎样推进";
  const note = currentRun() ? "每一步都有明确产出和边界" : "启动后会按这条路径逐步产出";
  return `<section class="section-block" id="agent-timeline"><div class="section-heading"><div><span class="eyebrow">THE RESEARCH PATH</span><h2>${title}</h2></div><span class="section-note">${note}</span></div><div class="timeline">${stages.map(([key, title, description], index) => `<article class="timeline-item ${currentStageClass(key)}"><div class="timeline-marker">${currentStageClass(key) === "completed" ? "✓" : index + 1}</div><div class="timeline-copy"><div class="timeline-title"><strong>${title}</strong><span>${currentStageClass(key) === "current" ? "当前阶段" : currentStageClass(key) === "completed" ? "已完成" : currentStageClass(key) === "failed" ? "需要处理" : "等待中"}</span></div><p>${description}</p></div></article>`).join("")}</div></section>`;
}

function renderRunMetrics() {
  const run = currentRun();
  const snapshot = state.runSnapshot;
  const budget = snapshot?.budget;
  const artifacts = run?.artifact_references?.length || 0;
  const pendingActions = (run?.human_actions || []).filter((action) => action.status === "pending").length;
  const metrics = [
    ["当前阶段", run ? stageLabel(run.stage) : "尚未启动", run ? statusLabel(run.status) : "等待研究问题"],
    ["已生成产物", String(artifacts), artifacts ? "可进入研究产物查看" : "运行后逐步出现"],
    ["待人工处理", String(pendingActions), pendingActions ? "需要你的判断" : "暂无阻塞事项"],
    ["执行预算", budget ? `${budget.used_steps} / ${budget.max_steps}` : "—", budget ? `${budget.used_replans} 次重规划` : "启动后初始化"],
  ];
  return `<section class="metrics-strip" aria-label="运行摘要">${metrics.map(([label, value, note]) => `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join("")}</section>`;
}

function renderPlan() {
  const plan = state.runSnapshot?.plan;
  const steps = plan?.steps || [];
  const content = steps.length
    ? `<ol class="plan-list">${steps.map((step) => `<li class="plan-step ${escapeHtml(step.status)}"><span class="step-icon">${step.status === "completed" ? "✓" : step.status === "running" ? "•" : "○"}</span><div><strong>${escapeHtml(step.tool_name)}</strong><p>${escapeHtml(step.success_criteria || "等待 Agent 执行")}</p>${step.result_summary ? `<small>${escapeHtml(step.result_summary)}</small>` : ""}</div></li>`).join("")}</ol>`
    : `<div class="empty-state compact"><strong>计划尚未生成</strong><p>启动运行后，Agent 会先提交结构化计划，再由本地策略守卫检查。</p></div>`;
  return `<section class="section-block split-section" id="agent-plan"><div class="section-heading"><div><span class="eyebrow">CURRENT PLAN</span><h2>下一步做什么</h2></div>${state.runSnapshot?.budget ? `<span class="budget-badge">${state.runSnapshot.budget.used_replans} / ${state.runSnapshot.budget.max_replans} 次规划</span>` : ""}</div><div class="panel plan-panel"><div class="panel-top"><span>${plan ? `计划版本 ${escapeHtml(String(plan.revision))}` : "尚未建立计划"}</span><span>计划只是提案，执行前必须通过策略校验</span></div>${content}</div></section>`;
}

function externalLink(url, label) {
  return /^https?:\/\//i.test(String(url || ""))
    ? `<a class="text-button" target="_blank" rel="noreferrer" href="${escapeHtml(url)}">${escapeHtml(label)}</a>`
    : "";
}

function renderPaperContext(paper) {
  if (!paper) return `<p class="paper-missing">未找到该文献的题录信息，请使用文献 ID人工核对。</p>`;
  const authors = Array.isArray(paper.authors) ? paper.authors.join("、") : "作者待核对";
  const meta = [paper.year || "年份待核对", paper.venue || "期刊待核对", authors].join(" · ");
  const screening = paper.relevance_score
    ? `<div class="screening-note"><strong>Agent 初步判断</strong><span>相关性 ${escapeHtml(paper.relevance_score)} / 10 · ${escapeHtml(paper.evidence_level || "证据等级待定")}</span><p>${escapeHtml(paper.rationale || "暂无判断说明")}</p>${paper.human_review_note ? `<small>需要核对：${escapeHtml(paper.human_review_note)}</small>` : ""}</div>`
    : "";
  return `<div class="review-paper"><div class="paper-card-top"><span class="candidate-tag">待审核文献</span><span>${escapeHtml(paper.paper_id || "")}</span></div><h4>${escapeHtml(paper.title || "题名待核对")}</h4><p class="paper-meta">${escapeHtml(meta)}</p>${paper.doi ? `<p class="paper-meta">DOI：${escapeHtml(paper.doi)}</p>` : ""}<div class="review-abstract"><span>摘要</span><p>${escapeHtml(paper.abstract || "暂无摘要，请打开来源核对。")}</p></div>${screening}<div class="review-links">${externalLink(paper.openalex_url, "打开 OpenAlex")}${externalLink(paper.open_access_url || paper.open_access_pdf_url, "查看公开版本")}</div></div>`;
}

function renderHumanGate() {
  const actions = (currentRun()?.human_actions || []).filter((action) => action.status === "pending");
  if (!actions.length) {
    return `<section class="section-block human-gate-empty-section" id="human-gate"><div class="human-clear"><span class="clear-icon">✓</span><div><strong>目前没有待人工处理事项</strong><p>当 Agent 无法在证据边界内自动决定时，会在这里暂停并明确说明原因。</p></div></div></section>`;
  }
  return `<section class="section-block" id="human-gate"><div class="section-heading"><div><span class="eyebrow">YOUR DECISION</span><h2>需要你的判断</h2></div><span class="attention-badge">${actions.length} 项待处理</span></div><div class="gate-list">${actions.map((action) => { const fulltext = action.allowed_decisions.includes("provide_fulltext"); const papers = action.paper_context || []; return `<article class="gate-card"><div class="gate-card-head"><span class="gate-type">${escapeHtml(action.action_type === "screening_review_required" ? "相关性审核" : "全文处理")}</span><span>${escapeHtml(stageLabel(action.stage))}</span></div><h3>${escapeHtml(action.reason)}</h3>${papers.map(renderPaperContext).join("")}<p class="paper-id-fallback">文献 ID：${escapeHtml(action.related_paper_ids.join("、") || "当前阶段产物")}</p><label for="decision-${escapeHtml(action.action_id)}">你的决定</label><select id="decision-${escapeHtml(action.action_id)}" data-decision-for="${escapeHtml(action.action_id)}">${action.allowed_decisions.map((decision) => `<option value="${escapeHtml(decision)}">${escapeHtml(decisionLabels[decision] || decision)}</option>`).join("")}</select>${fulltext ? `<div class="fulltext-upload"><label for="fulltext-${escapeHtml(action.action_id)}">如果你已合法取得全文，请选择文件</label><input id="fulltext-${escapeHtml(action.action_id)}" data-fulltext-for="${escapeHtml(action.action_id)}" type="file" accept=".pdf,.md,.txt,application/pdf,text/markdown,text/plain" /><label class="checkbox-label"><input type="checkbox" data-lawful-for="${escapeHtml(action.action_id)}" />我确认该文件由我合法取得，并有权在本地处理</label></div>` : ""}<label for="note-${escapeHtml(action.action_id)}">备注（可选）</label><textarea id="note-${escapeHtml(action.action_id)}" data-note-for="${escapeHtml(action.action_id)}" rows="3" placeholder="补充你的判断依据"></textarea>${button("提交决定并继续", `resolve-${escapeHtml(action.action_id)}`, "primary")}</article>`; }).join("")}</div></section>`;
}

function renderArtifacts() {
  const run = currentRun();
  const types = new Set(run?.artifact_references?.map((item) => item.artifact_type) || []);
  const artifactCards = [
    ["search_results", "检索结果", "候选题录与查询轮次"],
    ["fulltext_metadata", "全文队列", "合法公开版本线索"],
    ["evidence_card", "证据卡", "逐篇研究结果"],
    ["evidence_synthesis", "证据综合", "研究间比较与推断边界"],
    ["review_draft", "研究草稿", "可继续核查的结构化草稿"],
  ];
  return `<section class="section-block" id="agent-artifacts"><div class="section-heading"><div><span class="eyebrow">RESEARCH OUTPUTS</span><h2>研究产物</h2></div><span class="section-note">产物保存在当前 run 下，可追溯到来源</span></div><div class="artifact-grid">${artifactCards.map(([type, title, description]) => `<article class="artifact-card ${types.has(type) ? "available" : "pending"}"><span class="artifact-icon">${types.has(type) ? "✓" : "○"}</span><div><strong>${title}</strong><p>${types.has(type) ? "已生成，可进入工作台查看" : description}</p></div>${types.has(type) ? `<a href="#${type === "search_results" ? "literature" : type === "evidence_card" ? "evidence" : "evidence"}">查看 →</a>` : ""}</article>`).join("")}</div></section>`;
}

function renderEvents() {
  const events = (state.runSnapshot?.events || []).slice().reverse().slice(0, 6);
  return `<section class="section-block split-section" id="agent-activity"><div class="section-heading"><div><span class="eyebrow">TRACEABLE ACTIVITY</span><h2>最近发生了什么</h2></div><a class="text-link" href="#events">查看完整事件</a></div><div class="panel event-panel">${events.length ? events.map((event) => `<div class="event-row"><span class="event-line ${escapeHtml(event.event_type)}"></span><div><strong>${escapeHtml(event.summary)}</strong><small>${escapeHtml(formatDate(event.occurred_at))}${event.tool_name ? ` · ${escapeHtml(event.tool_name)}` : ""}</small></div></div>`).join("") : `<div class="empty-state compact"><strong>暂无事件</strong><p>Agent 启动后，计划、工具调用和人工决策会以摘要形式记录在这里。</p></div>`}</div></section>`;
}

function renderIntakeBrief() {
  const brief = state.intakeBrief;
  if (!brief) return "";
  const fields = [
    ["人群", brief.population],
    ["干预 / 暴露", brief.intervention_or_exposure],
    ["结局", (brief.outcomes || []).join("、")],
    ["研究类型", (brief.study_types || []).join("、")],
    ["年份", brief.year_from || brief.year_to ? `${brief.year_from || "不限"}–${brief.year_to || "至今"}` : "未限定"],
  ].filter((item) => item[1]);
  const question = brief.research_question || "研究助手还没有整理出候选研究问题。";
  const questionContent = state.intakeBriefEditing
    ? `<textarea id="brief-question" rows="3" maxlength="4000">${escapeHtml(brief.research_question || "")}</textarea>`
    : `<p class="brief-question-display">${escapeHtml(question)}</p>`;
  const briefStatus = state.intakeFallback ? "需要手动确认" : state.intakeReady ? "可以启动" : "还需要补充";
  const briefIntro = state.intakeFallback
    ? "研究助手暂时没有返回结构化结果，下面保留的是你的原始想法。请手动核对后再启动。"
    : "我先把当前讨论整理成一个可检索的研究任务，你可以在启动前核对。";
  return `<div class="chat-message assistant brief-message"><div class="chat-message-head"><span>研究助手</span><span class="brief-status ${state.intakeFallback ? "fallback" : ""}">${briefStatus}</span></div><p class="brief-intro">${briefIntro}</p>${state.intakeFallback ? `<div class="brief-warning" role="status">这不是自动确认结果；启动前请检查研究问题、年份和检索模式。</div>` : ""}<div class="intake-brief-card"><div class="brief-question-head"><strong>候选研究问题</strong><button class="text-button" id="edit-intake-brief" type="button">${state.intakeBriefEditing ? "完成编辑" : "修改研究问题"}</button></div>${questionContent}<div class="brief-fields">${fields.map(([label, value]) => `<span><small>${escapeHtml(label)}</small>${escapeHtml(value)}</span>`).join("")}</div></div>${state.intakeReady ? `<p class="brief-confirm-note">确认后才会创建 Agent 运行，当前对话不会直接触发检索。</p>` : ""}</div>`;
}

function renderChatMessage(role, content) {
  const roleClass = role === "user" ? "user" : "assistant";
  const label = roleClass === "assistant" ? "研究助手" : "你";
  return `<div class="chat-message ${roleClass}"><div class="chat-message-head"><span>${label}</span></div><p>${escapeHtml(content)}</p></div>`;
}

function renderIntakePanel() {
  const messages = state.intakeMessages.map((message) => renderChatMessage(message.role, message.content)).join("");
  const welcome = renderChatMessage("assistant", "先告诉我你想研究什么，即使想法还很模糊也可以。我会先帮你澄清人群、干预和结局，再由你确认是否启动检索。");
  const panelMode = state.intakeMessages.length ? "has-history" : "initial-state";
  return `<div class="intake-panel ${panelMode}"><div class="intake-panel-head"><div><span class="eyebrow">RESEARCH INTAKE</span><h3>和研究助手对话</h3><p>从模糊想法开始，逐步整理成可检索、可审核的研究问题。</p></div><span class="intake-status"><i></i>人工确认后启动</span></div><div class="chat-transcript" role="log" aria-label="研究助手对话记录" aria-live="polite">${welcome}${messages}${renderIntakeBrief()}${state.intakeLoading ? `<div class="chat-message assistant loading-message"><div class="chat-message-head"><span>研究助手</span></div><p><i class="mini-spinner"></i>正在整理你的研究想法…</p></div>` : ""}</div><form id="research-intake" class="intake-form"><label for="intake-message">继续描述你的研究想法</label><textarea id="intake-message" rows="3" maxlength="4000" aria-describedby="intake-hint" placeholder="例如：我想研究老年人跌倒恐惧和心理干预，但还不确定应该关注哪些指标。" ${state.intakeLoading ? "disabled" : ""}>${escapeHtml(state.intakeDraft)}</textarea><div class="compose-footer"><span id="intake-hint" class="input-hint">Enter 换行 · Ctrl + Enter 发送</span><div class="form-actions"><button class="button secondary" id="clear-intake" type="button">清空对话</button><button class="button primary" type="submit">${state.intakeLoading ? "整理中…" : "发送给研究助手"}</button></div></div></form></div>`;
}

function captureSearchConsoleControls() {
  const manualQuery = document.querySelector("#manual-search-query")?.value.trim();
  const yearFrom = document.querySelector("#search-year-from")?.value.trim();
  const yearTo = document.querySelector("#search-year-to")?.value.trim();
  const candidateLimit = document.querySelector("#search-candidate-limit")?.value;
  if (manualQuery !== undefined) state.searchPreferences.manual_query = manualQuery;
  if (candidateLimit) state.searchPreferences.candidate_limit = Number(candidateLimit) || 30;
  if (state.intakeBrief && (yearFrom !== undefined || yearTo !== undefined)) {
    state.intakeBrief = {
      ...state.intakeBrief,
      year_from: yearFrom ? Number(yearFrom) : null,
      year_to: yearTo ? Number(yearTo) : null,
    };
  }
}

function selectedModelProvider() {
  return modelProviderPresets[state.modelConnection.provider] || modelProviderPresets.codex_cli;
}

function renderModelSelector(locked = false) {
  const provider = state.modelConnection.provider;
  return `<div class="model-selector"><label for="model-provider">运行模型</label><select id="model-provider" ${locked ? "disabled" : ""}><optgroup label="本机运行"><option value="codex_cli" ${provider === "codex_cli" ? "selected" : ""}>本机 Codex CLI（默认）</option></optgroup><optgroup label="云端 API"><option value="openai" ${provider === "openai" ? "selected" : ""}>OpenAI API</option><option value="deepseek" ${provider === "deepseek" ? "selected" : ""}>DeepSeek API</option><option value="qwen" ${provider === "qwen" ? "selected" : ""}>阿里云百炼 / Qwen</option><option value="custom" ${provider === "custom" ? "selected" : ""}>其他 OpenAI-compatible</option></optgroup></select></div>`;
}

function renderModelConnectionPanel() {
  const connection = state.modelConnection;
  const provider = selectedModelProvider();
  if (!provider.external) {
    return state.modelSettingsOpen
      ? `<div class="model-connection-panel local-model-note"><div><span class="eyebrow">LOCAL MODEL</span><strong>本机 Codex CLI 无需填写云端 API</strong><p>它沿用本机 Codex 登录与只读沙箱设置；模型只提出结构化计划，工具仍由项目策略层授权。</p></div></div>`
      : "";
  }
  return `<div class="model-connection-panel" aria-label="模型 API 配置"><div class="model-connection-heading"><div><span class="eyebrow">MODEL CONNECTION</span><strong>${escapeHtml(provider.label)} 接入参数</strong><p>该模型同时用于研究问题整理、规划、筛选、证据提取和草稿生成。</p></div><span class="memory-only-badge">仅内存使用</span></div><div class="model-config-grid"><div class="model-config-field model-config-wide"><label for="model-api-base">API 地址</label><input id="model-api-base" type="url" maxlength="1000" value="${escapeHtml(connection.api_base)}" placeholder="https://provider.example/v1" autocomplete="url" /></div><div class="model-config-field"><label for="model-id">模型 ID</label><input id="model-id" maxlength="200" value="${escapeHtml(connection.model)}" placeholder="填写账号可用的模型 ID" autocomplete="off" /></div><div class="model-config-field"><label for="model-api-key">API Key</label><div class="secret-field"><input id="model-api-key" type="password" maxlength="4096" placeholder="仅用于当前页面和运行" autocomplete="new-password" spellcheck="false" /><button class="secret-toggle" id="toggle-model-secret" type="button" aria-label="显示或隐藏 API Key">显示</button></div></div><div class="model-config-field"><label for="model-timeout">单次请求超时（秒）</label><input id="model-timeout" type="number" min="10" max="600" value="${escapeHtml(connection.timeout_seconds)}" /></div><label class="model-json-option"><input id="model-json-mode" type="checkbox" ${connection.json_mode ? "checked" : ""} /><span><strong>启用 JSON 输出模式</strong><small>推荐开启；若自定义服务不支持 response_format，可关闭。</small></span></label></div><div class="model-security-note"><strong>安全说明</strong><span>密钥不会写入研究目标、运行事件或证据产物；页面刷新或服务重启后可能需要重新输入。</span></div></div>`;
}

function renderSearchConsole() {
  const run = currentRun();
  const preferences = state.runSnapshot?.goal?.search_preferences || state.searchPreferences;
  if (run) {
    const brief = state.runSnapshot?.research_brief || state.intakeBrief || {};
    const yearText = brief.year_from || brief.year_to
      ? `${brief.year_from || "不限"}–${brief.year_to || "至今"}`
      : "未限定";
    const queryText = preferences.mode === "manual"
      ? preferences.manual_query || "手动检索式未记录"
      : "由 Agent 根据已确认研究问题生成并迭代检索式";
    const model = state.runSnapshot?.model_connection;
    const modelText = model?.provider === "codex_cli" || !model
      ? "本机 Codex CLI"
      : `${modelProviderPresets[model.provider]?.label || model.provider} · ${model.model || "模型待确认"}`;
    return `<section class="search-console locked" id="search-console"><div class="search-console-head"><div><span class="eyebrow">SEARCH CONTROL</span><h3>检索控制台</h3><p>当前运行已经锁定检索条件；结果会进入同一条相关性筛选与证据链。</p></div><span class="search-lock">运行中不可修改</span></div><div class="search-console-status"><span class="status-dot ${escapeHtml(run.status)}"></span><div><strong>${escapeHtml(preferences.mode === "manual" ? "手动检索条件" : "AI 自动检索")}</strong><p>${escapeHtml(queryText)}</p><small>年份：${escapeHtml(yearText)} · 候选上限：${escapeHtml(preferences.candidate_limit || 30)} 篇 · 模型：${escapeHtml(modelText)}</small></div></div><div class="search-console-footer"><span>需要调整条件或模型时，请从 Agent 总览重新启动一次研究。</span><a class="button secondary" href="#literature">查看检索结果</a></div></section>`;
  }
  const brief = state.intakeBrief || {};
  const yearFrom = brief.year_from || "";
  const yearTo = brief.year_to || "";
  const initialPreferences = state.searchPreferences || {};
  const mode = state.searchMode || initialPreferences.mode || "automatic";
  const manualQuery = initialPreferences.manual_query || "";
  const limit = initialPreferences.candidate_limit || 30;
  return `<section class="search-console" id="search-console"><div class="search-console-head"><div><span class="eyebrow">SEARCH CONTROL</span><h3>检索控制台</h3><p>可以让 Agent 自动生成检索式，也可以在确认研究问题后手动控制检索条件。</p></div><div class="search-mode-switch" role="tablist" aria-label="检索模式"><button class="search-mode-button ${mode === "automatic" ? "active" : ""}" data-search-mode="automatic" type="button" role="tab" aria-selected="${mode === "automatic"}">AI 自动检索</button><button class="search-mode-button ${mode === "manual" ? "active" : ""}" data-search-mode="manual" type="button" role="tab" aria-selected="${mode === "manual"}">手动控制</button></div></div><div class="search-console-body">${mode === "automatic" ? `<div class="search-console-note"><strong>自动模式</strong><span>Agent 会根据已确认的研究问题生成查询，并把检索到的候选文献送入后续相关性筛选。你仍然可以限制年份和候选数量。</span></div>` : `<div class="search-console-note manual"><strong>手动模式</strong><span>输入精确检索式；Agent 仍会负责结果去重、相关性筛选、人工审核和证据边界控制。</span></div><div class="search-console-query"><label for="manual-search-query">手动检索式</label><input id="manual-search-query" value="${escapeHtml(manualQuery)}" minlength="3" maxlength="500" placeholder="例如：older adults AND fear of falling AND psychological intervention" /></div>`}<div class="search-console-grid"><div class="search-console-field"><label for="search-year-from">起始年份</label><input id="search-year-from" type="number" min="1900" max="2100" value="${escapeHtml(yearFrom)}" placeholder="不限" /></div><div class="search-console-field"><label for="search-year-to">截止年份</label><input id="search-year-to" type="number" min="1900" max="2100" value="${escapeHtml(yearTo)}" placeholder="至今" /></div><div class="search-console-field"><label for="search-candidate-limit">候选数量上限</label><input id="search-candidate-limit" type="number" min="1" max="100" value="${escapeHtml(limit)}" /></div></div><div class="search-console-footer"><span>${state.intakeReady ? "确认检索条件和运行模型后，再由你启动。" : "请先在上方对话中确认研究问题。"}</span><div class="model-launch-controls">${renderModelSelector()}<button class="button model-settings-button" id="toggle-model-settings" type="button">${selectedModelProvider().external ? "API 配置" : "模型说明"}</button><button class="button primary" id="start-search-console" type="button" ${state.intakeReady && !state.loading ? "" : "disabled"}>${state.loading ? "正在启动…" : "人工确认并开始检索"}</button></div></div>${renderModelConnectionPanel()}</div></section>`;
}

function renderConnectionSettings() {
  const required = state.capabilities?.api_key_required;
  return `<details class="advanced-options" ${required ? "open" : ""}><summary>连接与高级设置</summary><div class="connection-status ${required ? "needs-key" : "ready"}"><strong>${required ? "需要本地服务访问口令" : "本地服务已连接"}</strong><p>${required ? "这是本地服务的访问口令，不是 OpenAI API Key。它由启动服务的人设置。" : "当前是本机模式，通常不需要填写 API Key。"}</p></div><label for="api-key">本地服务访问口令（可选）</label><input id="api-key" form="research-intake" type="password" placeholder="PEA_API_KEY 未设置时无需填写" autocomplete="off" /><p>如果服务返回 401，输入启动服务时设置的 PEA_API_KEY；口令只保存在本次浏览器会话。</p></details>`;
}

function renderOverview() {
  const run = currentRun();
  const runMode = !run
    ? "pre-run"
    : run.status === "waiting_for_human"
      ? "needs-review"
      : ["completed", "failed", "cancelled"].includes(run.status)
        ? "finished"
        : "active";
  return `${pageIntro("BOUNDED RESEARCH AGENT", "从研究问题出发，沿着证据链走到可审查的结论。", "这是 Agent 的总览页：你可以看到它正在做什么、为什么暂停，以及每一步产生了什么。复杂的文献处理会进入独立工作台。")}
<section class="hero-grid overview-layout" id="run-start"><div class="hero-copy"><div class="assistant-intro"><div class="assistant-intro-copy"><span class="hero-kicker">研究问题助手</span><h2>先把问题说清楚，<br /><em>再让 Agent 开始工作。</em></h2><p>你可以从模糊想法开始。助手会帮你澄清研究对象、干预或暴露、结局指标和研究范围；只有你确认后，才会创建搜索运行。</p></div><div class="assistant-capabilities"><span>澄清研究对象</span><span>整理研究问题</span><span>确认后才检索</span></div></div>${renderIntakePanel()}${renderSearchConsole()}</div><div class="run-history-row"><aside class="hero-status"><div class="status-card-label">LIVE RESEARCH RUN</div>${renderRunSummary()}<div class="safety-strip"><span>◈</span><span>模型只提出计划，工具执行前由本地策略守卫授权。</span></div></aside><aside class="past-runs-card"><span class="eyebrow">PAST RUNS</span><strong>查看以往研究结果</strong><p>回到过去的运行，继续审核和查看已经生成的证据产物。</p><button class="history-entry button secondary" id="open-history" type="button">查看历史结果</button></aside></div>${renderConnectionSettings()}</section>
<div class="overview-alert">${noticeMarkup()}</div><div class="overview-dashboard ${runMode}"><div class="overview-process-shell">${renderTimeline()}${renderRunMetrics()}</div><div class="overview-two-column">${renderPlan()}${renderHumanGate()}</div>${renderArtifacts()}${renderEvents()}</div>
<section class="closing-banner"><span class="eyebrow">EVIDENCE WITH CARE</span><h2>记录每一个结论是如何得来的。</h2><p>题录不是证据，摘要不是全文，推断也不是直接发现。工作台会保留这些边界。</p></section>`;
}

function renderSearchForm() {
  return `<form id="literature-search" class="search-form panel"><div class="form-field wide"><label for="search-query">检索式</label><input id="search-query" value="${escapeHtml(state.query || state.question)}" minlength="3" maxlength="500" required /></div><div class="form-field"><label for="year-from">起始年份</label><input id="year-from" type="number" min="1900" max="2100" value="${state.yearFrom}" /></div><div class="form-field"><label for="max-results">数量</label><input id="max-results" type="number" min="1" max="25" value="${state.maxResults}" /></div>${button(state.loading ? "检索中…" : "检索 OpenAlex", "search-button", "primary", "submit")}</form>`;
}

function paperCard(paper, index) {
  return `<article class="paper-card"><div class="paper-card-top"><span class="candidate-tag">Agent 检索候选</span><span>#${index + 1}</span></div><h3>${escapeHtml(paper.title || "未命名题录")}</h3><p class="paper-meta">${escapeHtml(paper.year || "年份待核对")} · ${escapeHtml(paper.venue || "来源待核对")} · 被引 ${escapeHtml(paper.cited_by_count ?? 0)}</p><p class="paper-disclaimer">这是当前运行的题录候选；相关性筛选和纳入决定会在后续阶段完成。</p><button class="text-button paper-detail" data-paper-id="${escapeHtml(paper.paper_id)}" type="button">查看题录详情 →</button></article>`;
}

function renderLiterature() {
  const run = currentRun();
  if (!run) {
    return `${pageIntro("SEARCH RESULTS", "查看 Agent 找到的文献。", "检索结果属于具体研究运行；它们会先作为题录候选展示，再进入相关性筛选和人工审核。")}${noticeMarkup()}<div class="empty-state panel"><strong>尚未选择研究运行</strong><p>请先在 Agent 总览确认研究问题并启动一次运行，或从历史结果打开已有运行。</p><div class="inline-actions"><a class="button primary" href="#overview">返回 Agent 总览</a><a class="button secondary" href="#history">查看历史结果</a></div></div>`;
  }
  const papers = state.searchResults;
  const meta = state.searchResultsMeta || {};
  const report = meta.report || {};
  const preferences = meta.search_preferences || {};
  const querySummary = preferences.mode === "manual"
    ? `手动检索 · ${preferences.manual_query || "检索式未记录"}`
    : "AI 根据研究问题生成检索式";
  const content = state.searchResultsLoading
    ? `<div class="empty-state panel"><i class="spinner"></i><strong>正在读取当前运行的检索结果…</strong></div>`
    : papers.length
      ? `<div class="paper-grid">${papers.map(paperCard).join("")}</div>`
      : `<div class="empty-state panel"><strong>${meta.status === "pending" ? "Agent 还没有完成检索" : "当前运行没有候选题录"}</strong><p>返回 Agent 总览查看当前阶段；完成检索后，候选文献会显示在这里。</p><a class="button secondary" href="#overview">查看运行进度</a></div>`;
  return `${pageIntro("SEARCH RESULTS", "查看 Agent 找到的文献。", `当前运行：${escapeHtml(run.run_id)} · 题录候选不会自动等同于证据。`)}${noticeMarkup()}<div class="search-results-summary panel"><div><span class="eyebrow">CURRENT SEARCH</span><strong>${escapeHtml(querySummary)}</strong><p>年份：${escapeHtml(meta.research_brief?.year_from || "不限")}–${escapeHtml(meta.research_brief?.year_to || "至今")} · 候选上限：${escapeHtml(preferences.candidate_limit || "—")} 篇</p></div><div class="search-result-stats"><span><strong>${escapeHtml(report.deduplicated_candidate_count ?? papers.length)}</strong>去重候选</span><span><strong>${escapeHtml(report.query_count ?? (meta.queries || []).length)}</strong>检索轮次</span><button class="button secondary" id="refresh-search-results" type="button">刷新结果</button></div></div><div class="workspace-toolbar"><div><strong>${papers.length ? `当前运行找到 ${papers.length} 篇候选题录` : "等待当前运行产出结果"}</strong><span>结果来自该 run 的 search_results.json，并将沿同一条 Agent 筛选链继续处理。</span></div><a class="text-link" href="#overview">返回 Agent 总览 →</a></div>${content}${state.selectedPaper ? renderPaperDetail() : ""}`;
}

function renderPaperDetail() {
  const paper = state.selectedPaper;
  return `<div class="modal-backdrop" id="paper-modal"><article class="paper-modal" role="dialog" aria-modal="true" aria-labelledby="paper-modal-title"><button class="modal-close" id="close-paper" type="button" aria-label="关闭">×</button><span class="candidate-tag">元数据候选 · 尚未筛选</span><h2 id="paper-modal-title">${escapeHtml(paper.title)}</h2><p class="modal-meta">${escapeHtml(paper.year || "待核对")} · ${escapeHtml(paper.venue || "来源待核对")} · 被引 ${escapeHtml(paper.cited_by_count ?? 0)}</p><div class="abstract-box"><span>摘要</span><p>${escapeHtml(paper.abstract || "该题录没有可用摘要，请通过来源链接人工核对。")}</p></div><div class="paper-detail-boundary"><strong>当前页面只展示题录元数据。</strong><span>它不会直接改变 Agent 的筛选决定；需要人工纳入、排除或提供全文时，请在当前运行的“人工审核”入口处理。</span></div><div class="modal-actions">${paper.openalex_url ? `<a class="button secondary" target="_blank" rel="noreferrer" href="${escapeHtml(paper.openalex_url)}">打开 OpenAlex 来源</a>` : ""}${paper.open_access_url ? `<a class="button secondary" target="_blank" rel="noreferrer" href="${escapeHtml(paper.open_access_url)}">查看公开版本</a>` : ""}<button class="button primary" id="close-paper-action" type="button">关闭详情</button></div></article></div>`;
}

function renderReview() {
  const actions = (currentRun()?.human_actions || []).filter((action) => action.status === "pending");
  return `${pageIntro("HUMAN REVIEW", "把需要经验的判断留给你。", "Agent 会明确指出它无法安全自动决定的地方。你的决定会写入当前运行，并成为后续继续执行的依据。")}${noticeMarkup()}${actions.length ? renderHumanGate() : `<div class="review-empty panel"><span class="clear-icon neutral">i</span><div><h2>当前没有待处理的人工审核</h2><p>这里不会把普通题录详情误报为审核任务。真正需要你决定时，Agent 会在当前运行中暂停并把文献和理由一起带到这里。</p><div class="inline-actions"><a class="button primary" href="#overview">查看 Agent 状态</a><a class="button secondary" href="#literature">打开文献工作台</a></div></div></div>`}`;
}

function renderHistory() {
  const content = state.historyLoading
    ? `<div class="empty-state panel"><i class="spinner"></i><strong>正在读取历史结果…</strong></div>`
    : state.history.length
      ? `<div class="history-grid">${state.history.map((run) => `<article class="history-card"><div class="history-card-top"><span class="status-dot ${escapeHtml(run.status)}"></span><span>${escapeHtml(statusLabel(run.status))} · ${escapeHtml(stageLabel(run.stage))}</span></div><h3>${escapeHtml(run.research_question)}</h3><p class="paper-meta">创建于 ${escapeHtml(formatDate(run.created_at))} · 更新于 ${escapeHtml(formatDate(run.updated_at))}</p><div class="history-stats"><span><strong>${escapeHtml(run.candidate_count ?? 0)}</strong>候选题录</span><span><strong>${escapeHtml(run.screened_count ?? 0)}</strong>已筛选</span><span><strong>${escapeHtml(run.pending_human_count ?? 0)}</strong>待处理</span></div><div class="history-actions"><button class="button primary open-history-run" data-run-id="${escapeHtml(run.run_id)}" type="button">打开结果</button><small>${escapeHtml(run.run_id)}</small></div></article>`).join("")}</div>`
      : `<div class="empty-state panel"><strong>还没有历史结果</strong><p>确认一个研究问题并启动 Agent 后，运行记录会自动出现在这里。</p><a class="button primary" href="#overview">开始一次研究</a></div>`;
  return `${pageIntro("HISTORY", "历史结果", "打开过去的研究运行，继续人工处理或查看已经生成的证据产物。")}${noticeMarkup()}<div class="history-toolbar"><span>${state.history.length ? `共 ${state.history.length} 次研究运行` : "运行记录保存在本机"}</span><button class="text-button" id="refresh-history" type="button">刷新历史</button></div>${content}`;
}

function screeningLabel(value) {
  return { direct: "直接证据", adjacent: "邻近证据", background: "背景证据", exclude: "排除" }[value] || value || "待定";
}

function renderScreening() {
  const run = currentRun();
  if (!run) {
    return `${pageIntro("SCREENING RESULTS", "相关性筛选", "先打开一个历史运行，才能查看它的逐篇筛选结果。")}${noticeMarkup()}<div class="empty-state panel"><strong>尚未选择运行</strong><p>返回 Agent 总览启动运行，或打开历史结果。</p><a class="button primary" href="#overview">返回 Agent 总览</a></div>`;
  }
  const content = state.screeningLoading
    ? `<div class="empty-state panel"><i class="spinner"></i><strong>正在读取筛选结果…</strong></div>`
    : state.screeningResults.length
      ? `<div class="screening-list">${state.screeningResults.map((paper, index) => `<article class="screening-card"><div class="paper-card-top"><span class="candidate-tag">${escapeHtml(paper.screening_status || "机器筛选完成")}</span><span>#${index + 1}</span></div><h3>${escapeHtml(paper.title || "题名待核对")}</h3><p class="paper-meta">${escapeHtml([paper.year || "年份待核对", paper.venue || "期刊待核对", Array.isArray(paper.authors) ? paper.authors.join("、") : "作者待核对"].join(" · "))}</p><div class="screening-summary"><span>相关性：${escapeHtml(paper.relevance_score || "—")} / 10</span><span>证据：${escapeHtml(screeningLabel(paper.evidence_level))}</span><span>主题：${escapeHtml(paper.subtopic || "待归类")}</span></div><p class="screening-rationale">${escapeHtml(paper.rationale || "暂无机器判断说明")}</p>${paper.human_review_note ? `<div class="screening-warning">需要人工核对：${escapeHtml(paper.human_review_note)}</div>` : ""}<div class="review-links">${externalLink(paper.openalex_url, "OpenAlex 来源")}${externalLink(paper.doi, "DOI")}${externalLink(paper.open_access_url || paper.open_access_pdf_url, "公开版本")}</div></article>`).join("")}</div>`
      : `<div class="empty-state panel"><strong>当前还没有完整筛选结果</strong><p>Agent 完成标题和摘要筛选后，结果会出现在这里；如果它正在等待人工判断，请先处理人工审核。</p><a class="button secondary" href="#review">查看人工审核</a></div>`;
  return `${pageIntro("SCREENING RESULTS", "相关性筛选结果", `当前运行：${escapeHtml(run.run_id)} · ${escapeHtml(run.research_question)}`)}${noticeMarkup()}<div class="screening-toolbar"><span>${state.screeningResults.length ? `已加载 ${state.screeningResults.length} 篇筛选结果` : "筛选结果会随运行推进逐步出现"}</span><button class="text-button" id="refresh-screening" type="button">刷新结果</button></div>${content}`;
}

function renderEvidence() {
  const run = currentRun();
  const references = run?.artifact_references || [];
  return `${pageIntro("EVIDENCE OUTPUTS", "从题录走向可核查的研究材料。", "证据产物会在 Agent 完成相应阶段后出现。每个产物都保留来源、研究设计和推断边界。")}${noticeMarkup()}<div class="evidence-dashboard"><div class="evidence-main panel"><div class="panel-top"><span>当前运行</span><strong>${run ? escapeHtml(run.run_id) : "尚未选择运行"}</strong></div>${references.length ? `<div class="reference-list">${references.map((reference) => `<div class="reference-row"><span class="artifact-icon">✓</span><div><strong>${escapeHtml(reference.artifact_type)}</strong><small>${escapeHtml(reference.logical_key)} · ${escapeHtml(formatDate(reference.created_at))}</small></div></div>`).join("")}</div>` : `<div class="empty-state"><strong>还没有证据产物</strong><p>先在 Agent 总览启动一次运行，或打开历史结果。</p><a class="button primary" href="#overview">返回 Agent 总览</a></div>`}</div><aside class="evidence-note"><span class="eyebrow">EVIDENCE RULE</span><h2>每一条结论都要回答三个问题。</h2><ol><li>原始来源在哪里？</li><li>研究设计支持什么强度的表述？</li><li>哪些内容是综合推断，而不是直接发现？</li></ol></aside></div>`;
}

function renderEventsPage() {
  const events = (state.runSnapshot?.events || []).slice().reverse();
  return `${pageIntro("RUN TRACE", "运行记录", "这里保留 Agent 的结构化运行摘要，不展示模型隐性思维，只展示可审计的操作事件。")}${events.length ? `<div class="event-history panel">${events.map((event) => `<div class="event-row"><span class="event-line ${escapeHtml(event.event_type)}"></span><div><strong>${escapeHtml(event.summary)}</strong><small>${escapeHtml(event.event_type)} · ${escapeHtml(formatDate(event.occurred_at))}${event.tool_name ? ` · ${escapeHtml(event.tool_name)}` : ""}</small></div></div>`).join("")}</div>` : `<div class="empty-state panel"><strong>还没有运行事件</strong><p>加载一个已有 Agent 运行后，这里会显示它的执行轨迹。</p></div>`}`;
}

function render() {
  captureIntakeViewport();
  const transientFormState = captureTransientFormState();
  const views = { overview: renderOverview, literature: renderLiterature, review: renderReview, screening: renderScreening, history: renderHistory, evidence: renderEvidence, events: renderEventsPage };
  state.view = views[state.view] ? state.view : "overview";
  app.innerHTML = views[state.view]();
  document.querySelectorAll(".main-nav a").forEach((link) => link.classList.toggle("active", link.dataset.view === state.view));
  bindEvents();
  const modelApiKey = document.querySelector("#model-api-key");
  if (modelApiKey) modelApiKey.value = state.modelConnection.api_key;
  restoreTransientFormState(transientFormState);
  const transcript = document.querySelector(".chat-transcript");
  if (transcript) {
    transcript.addEventListener("scroll", () => {
      const distanceFromBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
      state.intakeFollow = distanceFromBottom < 56;
      state.intakeScrollTop = transcript.scrollTop;
    });
    window.requestAnimationFrame(() => {
      transcript.scrollTop = state.intakeFollow ? transcript.scrollHeight : Math.min(state.intakeScrollTop, transcript.scrollHeight);
    });
  }
}

function captureTransientFormState() {
  const values = {};
  document.querySelectorAll("input, textarea, select").forEach((field) => {
    if (!field.id || field.type === "file") return;
    values[field.id] = field.type === "checkbox" ? field.checked : field.value;
  });
  const active = document.activeElement;
  return {
    values,
    activeId: active?.id || "",
    selectionStart: typeof active?.selectionStart === "number" ? active.selectionStart : null,
    selectionEnd: typeof active?.selectionEnd === "number" ? active.selectionEnd : null,
    modelProvider: state.modelConnection.provider,
  };
}

function restoreTransientFormState(snapshot) {
  if (!snapshot) return;
  const providerChanged = state.modelConnectionReset || snapshot.modelProvider !== state.modelConnection.provider;
  Object.entries(snapshot.values).forEach(([id, value]) => {
    if (providerChanged && ["model-api-base", "model-id", "model-api-key", "model-timeout", "model-json-mode"].includes(id)) return;
    const field = document.getElementById(id);
    if (!field || field.type === "file") return;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else if (!field.disabled) field.value = value;
  });
  const active = snapshot.activeId ? document.getElementById(snapshot.activeId) : null;
  if (active && !active.disabled) {
    active.focus({ preventScroll: true });
    if (snapshot.selectionStart !== null && typeof active.setSelectionRange === "function") {
      active.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd ?? snapshot.selectionStart);
    }
  }
  state.modelConnectionReset = false;
}

function captureIntakeViewport() {
  const transcript = document.querySelector(".chat-transcript");
  if (!transcript) return;
  const distanceFromBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
  state.intakeFollow = distanceFromBottom < 56;
  state.intakeScrollTop = transcript.scrollTop;
}

function beginPolling() {
  window.clearInterval(pollTimer);
  if (!state.runId) return;
  pollTimer = window.setInterval(() => refreshRun(true), 3000);
}

function captureApiKey() {
  const value = document.querySelector("#api-key")?.value.trim() || "";
  if (value) sessionStorage.setItem("pea.api_key", value);
  else sessionStorage.removeItem("pea.api_key");
}

function captureModelConnectionFields() {
  const model = document.querySelector("#model-id");
  const apiBase = document.querySelector("#model-api-base");
  const apiKey = document.querySelector("#model-api-key");
  const timeout = document.querySelector("#model-timeout");
  const jsonMode = document.querySelector("#model-json-mode");
  if (model) state.modelConnection.model = model.value.trim();
  if (apiBase) state.modelConnection.api_base = apiBase.value.trim();
  if (apiKey) state.modelConnection.api_key = apiKey.value.trim();
  if (timeout) state.modelConnection.timeout_seconds = Number(timeout.value) || 120;
  if (jsonMode) state.modelConnection.json_mode = jsonMode.checked;
}

function modelConnectionPayload() {
  captureModelConnectionFields();
  const connection = state.modelConnection;
  if (connection.provider === "codex_cli") return { provider: "codex_cli" };
  if (!connection.model) throw new Error("请填写所选服务中可用的模型 ID。");
  if (!connection.api_base) throw new Error("请填写模型 API 地址。");
  if (!connection.api_key) throw new Error("请填写模型 API Key；密钥不会写入研究产物。");
  let parsed;
  try {
    parsed = new URL(connection.api_base);
  } catch {
    throw new Error("模型 API 地址格式不正确。");
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
    throw new Error("模型 API 地址必须使用 HTTPS；只有本机地址可以使用 HTTP。");
  }
  return {
    provider: connection.provider,
    model: connection.model,
    api_base: connection.api_base,
    api_key: connection.api_key,
    timeout_seconds: Math.min(600, Math.max(10, connection.timeout_seconds || 120)),
    json_mode: connection.json_mode,
  };
}

async function refreshRun(silent = false) {
  if (!state.runId) return;
  try {
    const snapshot = await api(`/v1/agent/runs/${encodeURIComponent(state.runId)}`);
    state.runSnapshot = snapshot;
    if (snapshot.goal?.search_preferences) {
      state.searchPreferences = snapshot.goal.search_preferences;
      state.searchMode = snapshot.goal.search_preferences.mode || "automatic";
    }
    if (!silent) state.notice = null;
    render();
  } catch (error) {
    if (!silent) setNotice(`加载运行失败：${error.message}`, "error");
    if (error.status === 404) {
      state.runId = "";
      state.runSnapshot = null;
      sessionStorage.removeItem("pea.run_id");
      window.clearInterval(pollTimer);
      if (!silent) setNotice("这次运行已不存在，可能已被清理。", "error");
    }
    render();
  }
}

function briefFromMessage(message) {
  return {
    research_question: message,
    population: "",
    intervention_or_exposure: "",
    comparison: "",
    outcomes: [],
    inclusion_criteria: [],
    exclusion_criteria: [],
    languages: [],
    study_types: [],
    year_from: null,
    year_to: null,
  };
}

async function sendIntakeMessage(event) {
  event.preventDefault();
  const input = document.querySelector("#intake-message");
  const message = input?.value.trim() || "";
  if (!message) {
    setNotice("先输入你想研究的问题或想法。", "error");
    render();
    return;
  }
  captureApiKey();
  let modelConnection;
  try {
    modelConnection = modelConnectionPayload();
  } catch (error) {
    state.modelSettingsOpen = true;
    setNotice(error.message, "error");
    render();
    return;
  }
  state.intakeMessages.push({ role: "user", content: message });
  state.intakeDraft = "";
  state.intakeFollow = true;
  state.intakeScrollTop = 0;
  state.intakeLoading = true;
  persistIntakeState();
  setNotice("研究助手正在整理问题，不会开始检索…");
  render();
  try {
    const payload = await api("/v1/research-intake/messages", {
      method: "POST",
      body: JSON.stringify({
        message,
        history: state.intakeMessages.slice(0, -1),
        current_brief: state.intakeBrief,
        model_connection: modelConnection,
      }),
    });
    state.intakeMessages.push({ role: "assistant", content: payload.assistant_message });
    state.intakeBrief = payload.candidate_brief;
    state.intakeFallback = false;
    state.intakeBriefEditing = false;
    state.intakeReady = Boolean(payload.ready_to_run);
    state.searchPreferences = payload.search_preferences || state.searchPreferences;
    state.searchMode = state.searchPreferences.mode || "automatic";
    state.question = payload.candidate_brief.research_question || message;
    saveQuestion();
    persistIntakeState();
    setNotice(state.intakeReady ? "研究问题已经整理完成，请确认后再启动 Agent。" : "研究助手需要你补充一点范围信息。", "success");
  } catch (error) {
    if (error.status === 401) {
      state.capabilities = { ...(state.capabilities || {}), api_key_required: true };
      state.intakeMessages.push({ role: "assistant", content: "需要本地服务访问口令。请在下方“连接与高级设置”中填写后重试。" });
      persistIntakeState();
      setNotice("服务需要本地访问口令，请先填写并保存。", "error");
    } else {
      state.intakeBrief = briefFromMessage(message);
      state.intakeFallback = true;
      state.intakeBriefEditing = false;
      state.intakeReady = true;
      state.searchPreferences = { mode: "automatic", manual_query: "", candidate_limit: 30 };
      state.searchMode = "automatic";
      state.intakeMessages.push({ role: "assistant", content: "研究助手暂时不可用。我已把你的原始想法放入候选研究问题，你可以手动修改并确认后启动。" });
      persistIntakeState();
      setNotice("已切换为手动确认模式；请检查候选研究问题。", "error");
    }
  }
  state.intakeLoading = false;
  persistIntakeState();
  render();
}

async function startAgentFromBrief() {
  const brief = state.intakeBrief;
  const editedQuestion = document.querySelector("#brief-question")?.value.trim() || "";
  const question = editedQuestion || brief?.research_question?.trim() || "";
  if (!brief || !state.intakeReady || !question) {
    setNotice("请先和研究助手确认一个清晰的研究问题。", "error");
    render();
    return;
  }
  captureApiKey();
  let modelConnection;
  try {
    modelConnection = modelConnectionPayload();
  } catch (error) {
    state.modelSettingsOpen = true;
    setNotice(error.message, "error");
    render();
    return;
  }
  const mode = state.searchMode || "automatic";
  const manualQuery = document.querySelector("#manual-search-query")?.value.trim() || "";
  const yearFromValue = document.querySelector("#search-year-from")?.value.trim() || "";
  const yearToValue = document.querySelector("#search-year-to")?.value.trim() || "";
  const candidateLimit = Math.min(100, Math.max(1, Number(document.querySelector("#search-candidate-limit")?.value) || 30));
  if (mode === "manual" && manualQuery.length < 3) {
    setNotice("手动模式需要填写至少 3 个字符的检索式。", "error");
    render();
    return;
  }
  const confirmedBrief = {
    ...brief,
    research_question: question,
    year_from: yearFromValue ? Number(yearFromValue) : null,
    year_to: yearToValue ? Number(yearToValue) : null,
  };
  if (confirmedBrief.year_from && confirmedBrief.year_to && confirmedBrief.year_from > confirmedBrief.year_to) {
    setNotice("起始年份不能晚于截止年份。", "error");
    render();
    return;
  }
  state.searchPreferences = { mode, manual_query: mode === "manual" ? manualQuery : "", candidate_limit: candidateLimit };
  state.searchMode = mode;
  state.intakeBrief = confirmedBrief;
  state.question = question;
  saveQuestion();
  state.intakeFallback = false;
  persistIntakeState();
  state.loading = true;
  setNotice("正在创建 Agent 运行…");
  render();
  try {
    const snapshot = await api("/v1/agent/runs", {
      method: "POST",
      body: JSON.stringify({
        objective: question,
        ...confirmedBrief,
        search_mode: mode,
        manual_query: mode === "manual" ? manualQuery : "",
        candidate_limit: candidateLimit,
        model_connection: modelConnection,
      }),
    });
    state.runSnapshot = snapshot;
    state.runId = snapshot.run.run_id;
    sessionStorage.setItem("pea.run_id", state.runId);
    state.view = "overview";
    setNotice("Agent 已启动。它会在后台推进，遇到需要判断的事项时暂停。", "success");
    beginPolling();
  } catch (error) {
    if (error.status === 401) {
      state.capabilities = { ...(state.capabilities || {}), api_key_required: true };
      setNotice("服务需要本地访问口令，请在连接设置中填写后重试。", "error");
    } else {
      setNotice(`启动失败：${error.message}`, "error");
    }
  }
  state.loading = false;
  render();
}

function clearIntake() {
  state.intakeMessages = [];
  state.intakeBrief = null;
  state.intakeReady = false;
  state.intakeFallback = false;
  state.intakeBriefEditing = false;
  state.intakeDraft = "";
  state.searchMode = "automatic";
  state.searchPreferences = { mode: "automatic", manual_query: "", candidate_limit: 30 };
  state.question = defaultQuestion;
  saveQuestion();
  state.intakeFollow = true;
  state.intakeScrollTop = 0;
  sessionStorage.removeItem("pea.intake_state");
  state.notice = null;
  render();
}

async function loadCapabilities() {
  try {
    state.capabilities = await api("/v1/system/capabilities");
    if (state.view === "overview") render();
  } catch {
    // The main page remains usable when a legacy server does not expose capabilities.
  }
}

async function loadHistory() {
  state.historyLoading = true;
  render();
  try {
    const payload = await api("/v1/agent/runs");
    state.history = payload.runs || [];
  } catch (error) {
    setNotice(`读取历史结果失败：${error.message}`, "error");
  }
  state.historyLoading = false;
  render();
}

async function openRun(runId) {
  state.runId = runId;
  sessionStorage.setItem("pea.run_id", runId);
  state.view = "overview";
  location.hash = "overview";
  await refreshRun();
  beginPolling();
}

async function loadScreeningResults() {
  if (!state.runId) return;
  state.screeningLoading = true;
  render();
  try {
    const payload = await api(`/v1/agent/runs/${encodeURIComponent(state.runId)}/screening-results`);
    state.screeningResults = payload.results || [];
  } catch (error) {
    state.screeningResults = [];
    if (error.status !== 404) setNotice(`读取筛选结果失败：${error.message}`, "error");
  }
  state.screeningLoading = false;
  render();
}

async function loadSearchResults() {
  if (!state.runId) return;
  state.searchResultsLoading = true;
  render();
  try {
    const payload = await api(`/v1/agent/runs/${encodeURIComponent(state.runId)}/search-results`);
    state.searchResults = payload.papers || [];
    state.searchResultsMeta = payload;
  } catch (error) {
    state.searchResults = [];
    state.searchResultsMeta = null;
    if (error.status !== 404) setNotice(`读取检索结果失败：${error.message}`, "error");
  }
  state.searchResultsLoading = false;
  render();
}

async function runAction(action) {
  if (!state.runId) return;
  state.loading = true;
  setNotice("正在提交运行操作…");
  render();
  try {
    const options = { method: "POST" };
    if (["resume", "retry"].includes(action) && state.modelConnection.provider !== "codex_cli" && state.modelConnection.api_key) {
      options.body = JSON.stringify({ model_connection: modelConnectionPayload() });
    }
    state.runSnapshot = await api(`/v1/agent/runs/${encodeURIComponent(state.runId)}/${action}`, options);
    setNotice(action === "cancel" ? "停止请求已记录，Agent 会在当前安全步骤结束后停止。" : "操作已提交，运行状态会自动刷新。", "success");
    beginPolling();
  } catch (error) {
    setNotice(`操作失败：${error.message}`, "error");
  }
  state.loading = false;
  render();
}

async function resolveAction(actionId) {
  const decision = document.querySelector(`[data-decision-for="${CSS.escape(actionId)}"]`)?.value;
  const note = document.querySelector(`[data-note-for="${CSS.escape(actionId)}"]`)?.value || "";
  const fileInput = document.querySelector(`[data-fulltext-for="${CSS.escape(actionId)}"]`);
  const file = fileInput?.files?.[0];
  const lawfulConfirmed = document.querySelector(`[data-lawful-for="${CSS.escape(actionId)}"]`)?.checked;
  if (decision === "provide_fulltext" && !file) {
    setNotice("请先选择你已合法取得的全文文件。", "error");
    render();
    return;
  }
  if (decision === "provide_fulltext" && !lawfulConfirmed) {
    setNotice("请确认你对该全文拥有合法访问和本地处理权限。", "error");
    render();
    return;
  }
  try {
    state.loading = true;
    setNotice("正在保存你的决定并继续运行…");
    render();
    const path = `/v1/agent/runs/${encodeURIComponent(state.runId)}/human-actions/${encodeURIComponent(actionId)}`;
    if (decision === "provide_fulltext") {
      const form = new FormData();
      form.append("file", file);
      form.append("lawful_access_confirmed", "true");
      form.append("note", note);
      state.runSnapshot = await api(`${path}/fulltext`, { method: "POST", body: form });
    } else {
      state.runSnapshot = await api(`${path}/resolve`, { method: "POST", body: JSON.stringify({ decision, note }) });
    }
    setNotice("人工决定已保存，Agent 将继续推进。", "success");
    beginPolling();
  } catch (error) {
    setNotice(`保存失败：${error.message}`, "error");
  }
  state.loading = false;
  render();
}

async function searchLiterature(event) {
  event.preventDefault();
  const query = document.querySelector("#search-query").value.trim();
  state.query = query;
  localStorage.setItem("pea.query", query);
  state.yearFrom = Number(document.querySelector("#year-from").value) || 2015;
  state.maxResults = Number(document.querySelector("#max-results").value) || 12;
  state.loading = true;
  setNotice("正在获取公开题录元数据…");
  render();
  try {
    const payload = await api("/v1/literature/search", { method: "POST", body: JSON.stringify({ query, year_from: state.yearFrom, max_results: state.maxResults, query_id: "web_research_search" }) });
    state.papers = payload.papers || [];
    setNotice(`已获取 ${state.papers.length} 篇元数据候选；这些结果尚未经相关性筛选。`, "success");
  } catch (error) {
    setNotice(`检索失败：${error.message}`, "error");
  }
  state.loading = false;
  render();
}

function bindEvents() {
  const intakeForm = document.querySelector("#research-intake");
  const intakeInput = document.querySelector("#intake-message");
  intakeForm?.addEventListener("submit", sendIntakeMessage);
  intakeInput?.addEventListener("input", (event) => { state.intakeDraft = event.target.value; persistIntakeState(); });
  intakeInput?.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      intakeForm.requestSubmit();
    }
  });
  document.querySelector("#clear-intake")?.addEventListener("click", clearIntake);
  document.querySelector("#edit-intake-brief")?.addEventListener("click", () => {
    captureSearchConsoleControls();
    state.intakeBriefEditing = !state.intakeBriefEditing;
    persistIntakeState();
    render();
    if (state.intakeBriefEditing) document.querySelector("#brief-question")?.focus();
  });
  document.querySelector("#brief-question")?.addEventListener("input", (event) => {
    if (!state.intakeBrief) return;
    state.intakeBrief = { ...state.intakeBrief, research_question: event.target.value };
    state.question = event.target.value;
    persistIntakeState();
  });
  document.querySelector("#start-search-console")?.addEventListener("click", startAgentFromBrief);
  document.querySelector("#model-provider")?.addEventListener("change", (event) => {
    captureSearchConsoleControls();
    captureModelConnectionFields();
    const provider = event.target.value;
    const changed = provider !== state.modelConnection.provider;
    state.modelConnection.provider = provider;
    if (changed) {
      state.modelConnection.model = "";
      state.modelConnection.api_key = "";
      state.modelConnection.api_base = modelProviderPresets[provider]?.api_base || "";
      state.modelConnectionReset = true;
    }
    state.modelSettingsOpen = Boolean(modelProviderPresets[provider]?.external);
    render();
  });
  document.querySelector("#toggle-model-settings")?.addEventListener("click", () => {
    captureSearchConsoleControls();
    captureModelConnectionFields();
    state.modelSettingsOpen = !state.modelSettingsOpen;
    render();
  });
  document.querySelector("#model-api-key")?.addEventListener("input", (event) => {
    state.modelConnection.api_key = event.target.value;
  });
  document.querySelector("#toggle-model-secret")?.addEventListener("click", (event) => {
    const input = document.querySelector("#model-api-key");
    if (!input) return;
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    event.currentTarget.textContent = showing ? "显示" : "隐藏";
  });
  document.querySelectorAll("[data-search-mode]").forEach((element) => element.addEventListener("click", () => {
    const manualQuery = document.querySelector("#manual-search-query")?.value.trim() || state.searchPreferences.manual_query || "";
    const yearFrom = document.querySelector("#search-year-from")?.value.trim();
    const yearTo = document.querySelector("#search-year-to")?.value.trim();
    if (state.intakeBrief && (yearFrom || yearTo)) {
      state.intakeBrief = {
        ...state.intakeBrief,
        year_from: yearFrom ? Number(yearFrom) : null,
        year_to: yearTo ? Number(yearTo) : null,
      };
    }
    const candidateLimit = Number(document.querySelector("#search-candidate-limit")?.value) || state.searchPreferences.candidate_limit || 30;
    state.searchMode = element.dataset.searchMode || "automatic";
    state.searchPreferences = { ...state.searchPreferences, mode: state.searchMode, manual_query: manualQuery, candidate_limit: candidateLimit };
    persistIntakeState();
    render();
  }));
  document.querySelector("#open-history")?.addEventListener("click", () => { location.hash = "history"; });
  document.querySelector("#refresh-history")?.addEventListener("click", loadHistory);
  document.querySelectorAll(".open-history-run").forEach((element) => element.addEventListener("click", () => openRun(element.dataset.runId)));
  document.querySelector("#refresh-screening")?.addEventListener("click", loadScreeningResults);
  document.querySelector("#refresh-search-results")?.addEventListener("click", loadSearchResults);
  document.querySelector("#refresh-run")?.addEventListener("click", () => refreshRun());
  document.querySelector("#jump-review")?.addEventListener("click", () => { location.hash = "review"; });
  document.querySelector("#cancel-run")?.addEventListener("click", () => runAction("cancel"));
  document.querySelector("#resume-run")?.addEventListener("click", () => runAction("resume"));
  document.querySelector("#retry-run")?.addEventListener("click", () => runAction("retry"));
  document.querySelector("#api-key")?.addEventListener("change", (event) => {
    const value = event.target.value.trim();
    if (value) sessionStorage.setItem("pea.api_key", value);
    else sessionStorage.removeItem("pea.api_key");
  });
  document.querySelector("#jump-to-run")?.addEventListener("click", () => { location.hash = "overview"; setTimeout(() => document.querySelector("#agent-timeline")?.scrollIntoView({ behavior: "smooth" }), 0); });
  document.querySelectorAll(".paper-detail").forEach((element) => element.addEventListener("click", () => { state.selectedPaper = state.searchResults.find((paper) => paper.paper_id === element.dataset.paperId) || state.papers.find((paper) => paper.paper_id === element.dataset.paperId) || null; render(); }));
  document.querySelector("#close-paper")?.addEventListener("click", () => { state.selectedPaper = null; render(); });
  document.querySelector("#paper-modal")?.addEventListener("click", (event) => { if (event.target.id === "paper-modal") { state.selectedPaper = null; render(); } });
  document.querySelector("#close-paper-action")?.addEventListener("click", () => { state.selectedPaper = null; render(); });
  document.querySelectorAll("[id^='resolve-']").forEach((element) => element.addEventListener("click", () => resolveAction(element.id.replace("resolve-", ""))));
}

window.addEventListener("hashchange", () => {
  state.view = location.hash.slice(1) || "overview";
  state.selectedPaper = null;
  render();
  if (state.view === "overview" || state.view === "review" || state.view === "evidence" || state.view === "events") refreshRun(true);
  if (state.view === "history") loadHistory();
  if (state.view === "literature") refreshRun(true).then(loadSearchResults);
  if (state.view === "screening") {
    refreshRun(true).then(loadScreeningResults);
  }
});

restoreIntakeState();
loadCapabilities();
render();
if (state.runId) {
  refreshRun(true).then(() => {
    if (state.view === "literature") loadSearchResults();
  });
  beginPolling();
}
