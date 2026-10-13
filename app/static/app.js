"use strict";
const $ = (id) => document.getElementById(id);
let current = null;
let resumeId = null;
let uploadedFile = null;
let busy = false;
let savedAPISettings = null;
let savedVoiceSettings = null;
let recording = null;
let recordedBlob = null;
let recordedDuration = 0;
let recordedTurnIndex = null;
let recordingId = null;
let historyOffset = 0;
let selectedPreparation = null;
let preparationOffset = 0;
let savedUsageSettings = null;
let usageOffset = 0;
const practiceRequests = new Map();
const statuses = {created: "等待联网研究", researched: "资料已保存，等待面试策略", prepared: "准备完成", active: "面试进行中", completed: "面试已结束"};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error("程序未返回有效结果，请检查本地服务后重试。"); }
  if (!response.ok) throw new Error(data.error || "请求失败，请重试。");
  return data;
}

function post(path, body) {
  return api(path, body === undefined ? {method: "POST"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
}

function message(text) { $("loading").textContent = text; $("loading").hidden = false; }
function showError(text) { $("error").textContent = text; $("error").hidden = false; }
function setBusy(value) {
  busy = value;
  document.querySelectorAll("button, #setup-form input, #setup-form select, #setup-form textarea, #api-settings-form input, #api-settings-form select, #voice-settings-form input, #voice-settings-form select, #usage-form input, #usage-form select").forEach((element) => { element.disabled = value || Boolean(recording); });
  $("answer").disabled = value || Boolean(recording);
  $("record-stop").disabled = value;
  if (!value && !recording && selectedPreparation) checkResearchMatch();
}

async function work(callback) {
  if (busy) return;
  setBusy(true);
  $("error").hidden = true;
  try { await callback(); }
  catch (error) {
    if (current) {
      try { render(await api(`/api/interviews/${encodeURIComponent(current.id)}`)); }
      catch { /* Keep the last known screen and the typed answer on network failure. */ }
      if (recordedBlob && recordedTurnIndex === current.current_turn_index && !current.needs_next_question) {
        $("transcribe-retry").hidden = false;
      }
    }
    showError(error.message);
  } finally {
    try { await loadUsage(); } catch { /* Do not hide the original action result. */ }
    setBusy(false); $("loading").hidden = true;
  }
}

function usagePriceContext() {
  const kind = $("usage-price-kind").value;
  const config = kind === "ai" ? savedAPISettings : savedVoiceSettings;
  if (!config || config.provider === "demo") return null;
  const values = config.providers[config.provider];
  return {kind, provider: config.provider, base_url: values.base_url, model: values.main_model || values.model};
}

function showUsagePrices() {
  const context = usagePriceContext();
  const voice = $("usage-price-kind").value === "voice";
  $("usage-token-prices").hidden = voice; $("usage-audio-prices").hidden = !voice;
  $("usage-price-context").textContent = context ? `${context.model} · ${context.base_url}` : "离线演示没有模型费用；可以单独保存预算提醒。";
  const price = context && savedUsageSettings?.prices.find((row) => Object.keys(context).every((key) => row[key] === context[key]));
  $("usage-input-price").value = price?.input_per_million ?? "";
  $("usage-output-price").value = price?.output_per_million ?? "";
  $("usage-audio-price").value = price?.audio_per_minute ?? "";
}

async function loadUsage() {
  const result = await api(`/api/usage?limit=30&offset=${usageOffset}`);
  if (!savedUsageSettings) {
    $("usage-request-limit").value = result.settings.monthly_request_limit ?? "";
    $("usage-budget").value = result.settings.monthly_budget_yuan ?? "";
    savedUsageSettings = result.settings; showUsagePrices();
  }
  const m = result.monthly;
  $("usage-summary").textContent = `${result.month}（北京时间）：${m.requests} 次请求（面试 ${m.ai_requests} / 语音 ${m.voice_requests}），失败 ${m.failed} / 结果待确认 ${m.pending}。已返回 token ${m.total_tokens}（输入 ${m.input_tokens} / 输出 ${m.output_tokens}）；${m.unknown_token_requests} 次 token 未知。累计转写 ${(m.audio_seconds / 60).toFixed(1)} 分钟。已知部分费用估算 ¥${m.estimated_yuan.toFixed(4)}，${m.incomplete_cost_requests} 次费用不完整。`;
  $("usage-scope").textContent = `开始记录于 ${new Date(result.since).toLocaleString("zh-CN")}；之前的消耗无法补算。离线演示、缓存和浏览历史不产生请求记录。统计只包含本程序，不包含平台上的其他应用。`;
  $("usage-warning").textContent = result.warnings.join(" "); $("usage-warning").hidden = !result.warnings.length;
  const parent = $("usage-list"); parent.replaceChildren();
  if (!result.items.length) paragraph(parent, "还没有 API 请求记录。");
  else {
    const table = document.createElement("table"); const head = document.createElement("tr");
    ["时间 / 用途", "模型 / 结果", "Token / 转写", "费用估算"].forEach((label) => { const th = document.createElement("th"); th.textContent = label; head.append(th); });
    const thead = document.createElement("thead"); thead.append(head); table.append(thead);
    const body = document.createElement("tbody");
    result.items.forEach((row) => {
      const tr = document.createElement("tr");
      const tokens = (value) => value === null ? "未知" : String(value);
      const values = [`${new Date(row.created_at).toLocaleString("zh-CN")} · ${row.operation}`,
        `${row.model || "模型列表"} · ${{success: "接口返回", failed: "请求失败", pending: "结果未知"}[row.status]}`,
        row.connection_check ? "模型列表检查" : row.kind === "voice" ? `${row.audio_seconds === null ? "时长未知" : (row.audio_seconds / 60).toFixed(2) + " 分钟"}` : `输入 ${tokens(row.input_tokens)} / 输出 ${tokens(row.output_tokens)}`,
        row.estimated_yuan === null ? "未知" : `¥${row.estimated_yuan.toFixed(4)}${row.partial_estimate ? "（部分）" : ""}`];
      values.forEach((value) => { const td = document.createElement("td"); td.textContent = value; tr.append(td); }); body.append(tr);
    }); table.append(body); parent.append(table);
  }
  $("usage-prev").hidden = !usageOffset; $("usage-next").hidden = usageOffset + result.items.length >= result.total;
}

$("usage-price-kind").addEventListener("change", showUsagePrices);
$("usage-refresh").addEventListener("click", () => work(loadUsage));
$("usage-prev").addEventListener("click", () => work(async () => { usageOffset = Math.max(0, usageOffset - 30); await loadUsage(); }));
$("usage-next").addEventListener("click", () => work(async () => { usageOffset += 30; await loadUsage(); }));
$("usage-form").addEventListener("submit", (event) => {
  event.preventDefault();
  work(async () => {
    const number = (id) => $(id).value.trim() === "" ? null : Number($(id).value);
    const context = usagePriceContext();
    let prices = [...(savedUsageSettings?.prices || [])];
    if (context) {
      prices = prices.filter((row) => !Object.keys(context).every((key) => row[key] === context[key]));
      const price = {...context, input_per_million: context.kind === "ai" ? number("usage-input-price") : null,
        output_per_million: context.kind === "ai" ? number("usage-output-price") : null,
        audio_per_minute: context.kind === "voice" ? number("usage-audio-price") : null};
      if ([price.input_per_million, price.output_per_million, price.audio_per_minute].some((value) => value !== null)) prices.push(price);
    }
    savedUsageSettings = await post("/api/usage/settings", {monthly_request_limit: number("usage-request-limit"), monthly_budget_yuan: number("usage-budget"), prices});
    showUsagePrices(); $("usage-settings-result").textContent = "提醒和单价已保存；新单价用于之后的请求。";
  });
});

function paragraph(parent, text) {
  const p = document.createElement("p"); p.textContent = text; parent.append(p);
}

function list(parent, title, items) {
  if (!items.length) return;
  const h = document.createElement("h3"); h.textContent = title; parent.append(h);
  const ul = document.createElement("ul");
  items.forEach((text) => { const li = document.createElement("li"); li.textContent = text; ul.append(li); });
  parent.append(ul);
}

function heading(parent, text, level = "h3") {
  const element = document.createElement(level); element.textContent = text; parent.append(element);
}

function preparationLabel(item) {
  const date = item.researched_at ? new Date(item.researched_at).toLocaleDateString("zh-CN") : "研究日期未记录";
  return `${item.company} · ${item.position} · ${item.interview_type} · ${date}${item.provider_mode === "demo" ? " · 离线演示" : ""}`;
}

function updateResumeRequirement() {
  $("resume").required = !($("use-saved-resume").checked && selectedPreparation?.resume);
}

function updateModeFields() {
  const training = $("practice-mode").value === "training";
  $("training-focus-option").hidden = !training; $("training-focus").required = training;
  $("group-options").hidden = $("practice-mode").value !== "group";
}

function clearPreparation() {
  selectedPreparation = null;
  $("preparation-select").value = "";
  $("preparation-detail").hidden = true;
  $("reuse-research-option").hidden = true; $("reuse-research").checked = false;
  $("saved-resume-option").hidden = true; $("use-saved-resume").checked = false;
  updateResumeRequirement();
}

async function loadPreparations() {
  const result = await api(`/api/preparations?limit=50&offset=${preparationOffset}`);
  if (preparationOffset >= result.total && preparationOffset) { preparationOffset = Math.max(0, preparationOffset - 50); return loadPreparations(); }
  clearPreparation();
  const select = $("preparation-select"); select.replaceChildren(new Option("新准备（重新联网）", ""));
  result.items.forEach((item) => select.add(new Option(preparationLabel(item), item.id)));
  $("preparation-count").textContent = result.total ? `本机保存了 ${result.total} 份${savedAPISettings.provider === "demo" ? "演示" : "联网"}准备资料。` : "还没有可用资料；准备新面试后会自动保存。";
  $("preparation-prev").hidden = preparationOffset === 0;
  $("preparation-next").hidden = preparationOffset + result.items.length >= result.total;
}

function showPreparation(value) {
  selectedPreparation = value;
  if (![...$("preparation-select").options].some((option) => option.value === value.id)) {
    $("preparation-select").add(new Option(preparationLabel(value), value.id));
  }
  $("preparation-select").value = value.id;
  const settings = value.settings;
  $("company").value = settings.company; $("position").value = settings.position;
  $("interview-type").value = settings.interview_type;
  if (![...$("duration").options].some((option) => option.value === String(settings.duration_minutes))) {
    $("duration").add(new Option(`${settings.duration_minutes} 分钟`, String(settings.duration_minutes)));
  }
  $("duration").value = settings.duration_minutes; $("notes").value = settings.notes;
  $("practice-mode").value = settings.mode || "simulation";
  updateModeFields();
  $("training-focus").value = settings.training_focus || "";
  $("group-size").value = settings.group_size || 5; $("group-topic").value = settings.group_topic || "";
  $("resume").value = ""; resumeId = null; uploadedFile = null;
  $("saved-resume-option").hidden = !value.resume;
  $("saved-resume-name").textContent = value.resume?.filename || "";
  $("use-saved-resume").checked = Boolean(value.resume); updateResumeRequirement();
  $("reuse-research-option").hidden = false;
  $("reuse-research").checked = true; $("reuse-research").disabled = false;
  $("reuse-research-status").textContent = "将复用已保存的资料。资料可能过时；取消勾选可重新联网。";
  const parent = $("preparation-detail"); parent.hidden = false; parent.replaceChildren();
  heading(parent, "已保存的准备资料");
  paragraph(parent, value.researched_at ? `研究于 ${new Date(value.researched_at).toLocaleString("zh-CN")}` : "这份旧资料没有记录研究日期，建议必要时重新联网。");
  if (value.provider_mode === "demo") paragraph(parent, "这是离线演示资料，没有真实联网研究。");
  const research = value.research;
  paragraph(parent, research.company_summary); paragraph(parent, research.position_summary);
  [["面试流程", research.interview_process], ["常见问题类型", research.common_question_types],
   ["技术考察", research.technical_topics], ["公司相关主题", research.company_specific_topics],
   ["行为面试主题", research.behavioral_topics], ["考察重点与招聘偏好", research.interview_preferences || []],
   ["尚未确认的信息", research.uncertainties]].forEach(([title, items]) => list(parent, title, items));
  heading(parent, "资料来源");
  if (!research.sources.length) paragraph(parent, "没有可核实的来源。");
  research.sources.forEach((source) => {
    try {
      const url = new URL(source.url);
      if (!["http:", "https:"].includes(url.protocol)) return;
      const p = document.createElement("p"); const link = document.createElement("a");
      link.href = url.href; link.textContent = source.title; link.target = "_blank"; link.rel = "noopener noreferrer";
      p.append(link); parent.append(p);
    } catch { /* Invalid legacy links are not clickable. */ }
  });
  $("preparation-library").open = true;
}

function researchMatchesSettings() {
  if (!selectedPreparation) return false;
  const normalize = (value) => value.trim().replace(/\s+/g, " ").toLowerCase();
  return normalize($("company").value) === normalize(selectedPreparation.company)
    && normalize($("position").value) === normalize(selectedPreparation.position)
    && $("interview-type").value === selectedPreparation.interview_type
    && ((selectedPreparation.provider_mode === "demo") === (savedAPISettings.provider === "demo"));
}

function checkResearchMatch() {
  if (!selectedPreparation) return;
  const matches = researchMatchesSettings();
  if (!matches) $("reuse-research").checked = false;
  $("reuse-research").disabled = !matches;
  $("reuse-research-status").textContent = matches
    ? "资料与当前公司、岗位和面试类型一致；取消勾选可重新联网。"
    : "公司、岗位或面试类型已改变，这次将重新联网。";
}

$("preparation-select").addEventListener("change", () => {
  const identifier = $("preparation-select").value;
  if (!identifier) { clearPreparation(); return; }
  work(async () => { clearPreparation(); showPreparation(await api(`/api/preparations/${encodeURIComponent(identifier)}`)); });
});
$("preparation-refresh").addEventListener("click", () => work(async () => { preparationOffset = 0; await loadPreparations(); }));
$("preparation-prev").addEventListener("click", () => work(async () => { preparationOffset = Math.max(0, preparationOffset - 50); await loadPreparations(); }));
$("preparation-next").addEventListener("click", () => work(async () => { preparationOffset += 50; await loadPreparations(); }));
$("use-saved-resume").addEventListener("change", updateResumeRequirement);
$("resume").addEventListener("change", () => { $("use-saved-resume").checked = false; updateResumeRequirement(); });
["company", "position", "interview-type"].forEach((id) => $(id).addEventListener(id === "interview-type" ? "change" : "input", checkResearchMatch));
$("delete-preparation").addEventListener("click", () => work(async () => {
  if (!selectedPreparation || !confirm(`删除 ${selectedPreparation.company} · ${selectedPreparation.position} 的这份准备资料及保存的设置？\n现有面试中的问答、简历和资料快照会保留。`)) return;
  await api(`/api/preparations/${encodeURIComponent(selectedPreparation.id)}`, {method: "DELETE"});
  await loadPreparations();
}));

function renderReport(parent, report, turns) {
  heading(parent, report.version >= 2 ? "面试复盘报告" : "基础复盘报告", "h2");
  paragraph(parent, report.overall_score === null ? "综合评分：暂无" : `综合评分：${report.overall_score}/100`);
  paragraph(parent, report.summary);
  list(parent, "优点", report.strengths);
  list(parent, "改进点", report.improvements);
  list(parent, "分项参考", report.scores.map((score) => `${score.dimension}：${score.score}/10。${score.evidence}`));
  if (report.group_statistics) {
    heading(parent, "无领导小组讨论记录");
    paragraph(parent, `你的发言共 ${report.group_statistics.user_contributions} 轮；${report.group_statistics.user_summary_submitted ? "已提交最终总结" : "本轮未提交最终总结"}。`);
    list(parent, "AI 队友发言轮次", report.group_statistics.teammates.map((member) => `${member.name}：${member.contributions} 次`));
    paragraph(parent, report.group_statistics.limitations);
  }
  const oral = report.oral_analysis;
  if (oral) {
    heading(parent, "口语统计");
    paragraph(parent, `语音回答 ${oral.voice_answers} 题；文字回答 ${oral.text_answers} 题。`);
    if (oral.timed_answers) paragraph(parent, `已知录音共 ${oral.total_duration_seconds} 秒，平均每题 ${oral.average_duration_seconds} 秒（${oral.timed_answers} 题有时长）。`);
    if (oral.timed_answers) paragraph(parent, `最长：第 ${oral.longest_answer} 题 ${oral.longest_duration_seconds} 秒；最短：第 ${oral.shortest_answer} 题 ${oral.shortest_duration_seconds} 秒。已知时长的录音中每分钟疑似口头词 ${oral.fillers_per_minute} 次。`);
    if (oral.voice_answers) paragraph(parent, `原始转写中的疑似口头词：${Object.entries(oral.filler_counts).map(([word, count]) => `${word} ${count} 次`).join("；")}。`);
    paragraph(parent, oral.limitations);
  }
  (report.answer_reviews || []).forEach((review) => {
    const detail = document.createElement("details"); detail.open = true;
    const summary = document.createElement("summary"); summary.textContent = report.group_statistics ? `第 ${review.turn_index} 次发言复盘` : `第 ${review.turn_index} 题复盘`; detail.append(summary);
    const turn = turns.find((item) => item.turn_index === review.turn_index);
    if (turn) { paragraph(detail, `问题：${turn.question}`); paragraph(detail, `你的回答：${turn.answer}`); }
    paragraph(detail, review.summary);
    const stats = oral?.turns.find((item) => item.turn_index === review.turn_index);
    if (stats) paragraph(detail, `已提交回答长度：${stats.answer_units} 个文本单位（一个汉字或一个英文/数字词为一单位）。`);
    if (stats?.source === "voice") {
      paragraph(detail, `录音：${stats.duration_seconds ?? "未知"} 秒；原始转写 ${stats.transcript_units} 单位；疑似口头词 ${stats.filler_total} 次。${stats.units_per_minute === null ? "" : `转写单位/分钟：${stats.units_per_minute}。`}`);
      if (stats.possible_repetitions.length) paragraph(detail, `疑似重复用词：${stats.possible_repetitions.join("、")}（需听录音确认）。`);
      if (turn?.transcript_raw) paragraph(detail, `原始转写：${turn.transcript_raw}`);
    }
    [["做得好的地方", review.strengths], ["可以改进的地方", review.issues], ["容易被追问的表述", review.risks]].forEach(([title, points]) => {
      if (!points.length) return;
      heading(detail, title, "h4");
      points.forEach((point) => { paragraph(detail, point.observation); paragraph(detail, `原文证据：“${point.evidence}”`); paragraph(detail, `建议：${point.suggestion}`); });
    });
    list(detail, "下次可以这样组织回答", review.suggested_structure);
    paragraph(detail, review.timing_advice);
    parent.append(detail);
  });
  list(parent, "优先练习建议", report.priority_practice || []);
  if (report.knowledge_gaps?.length) {
    heading(parent, "知识薄弱点与复习建议");
    report.knowledge_gaps.forEach((gap) => {
      heading(parent, `${gap.priority === "high" ? "高" : "中"}优先级：${gap.topic}`, "h4");
      paragraph(parent, gap.reason);
      gap.evidence.forEach((evidence) => paragraph(parent, `第 ${evidence.turn_index} 题原文：“${evidence.quote}”`));
      list(parent, "建议复习", gap.review_suggestions);
    });
  } else if (report.version >= 3) paragraph(parent, "本次没有足够证据确认具体知识薄弱点。");
  if (report.next_practice) {
    heading(parent, "下一轮练习计划");
    paragraph(parent, `建议：${report.next_practice.mode}；重点：${report.next_practice.focus}；时长：${report.next_practice.duration_minutes} 分钟。`);
    list(parent, "练习内容", report.next_practice.exercises);
    paragraph(parent, "可点击下方按钮，按本轮计划开始专项练习。");
  }
  paragraph(parent, report.limitations);
}

function groupMessages(parent, round, scenario) {
  if (!round) return;
  const names = Object.fromEntries(scenario.teammates.map(member => [member.member_id, member.name]));
  round.messages.forEach((message) => {
    const div = document.createElement("div"); div.className = "group-message";
    paragraph(div, `${names[message.member_id] || "AI 队友"}（AI）：${message.speech}`);
    if (message.reply_to_quote) paragraph(div, `回应你的原话：“${message.reply_to_quote}”`);
    parent.append(div);
  });
  if (round.discussion_summary) paragraph(parent, `讨论进展（AI 摘要）：${round.discussion_summary}`);
}

function render(session) {
  const oldIndex = current?.id === session.id ? current.current_turn_index : null;
  current = session;
  localStorage.setItem("ai-interviewer-session", session.id);
  const url = new URL(location.href); url.searchParams.set("session", session.id); history.replaceState(null, "", url);
  $("setup").hidden = true; $("interview").hidden = false;
  $("session-title").textContent = `${session.company} · ${session.position}`;
  $("session-status").textContent = `${session.interview_type} · ${statuses[session.status]}${session.provider_mode === "demo" ? " · 离线演示" : ""}`;
  if (session.mode === "training") $("session-status").textContent += " · 专项练习";
  if (session.mode === "group") $("session-status").textContent += " · 无领导小组讨论";
  if (session.mode === "group" && session.status === "researched") $("session-status").textContent = "资料已保存，等待讨论案例 · 无领导小组讨论";
  $("training-plan-status").hidden = session.mode !== "training";
  $("training-plan-status").textContent = session.training_plan ? `练习重点：${session.training_plan.focus} · 目标 ${session.training_plan.duration_minutes} 分钟` : "";
  if (session.research_origin === "reused") $("session-status").textContent += " · 使用本机已保存的准备资料";
  $("prepare").hidden = !["created", "researched", "prepared"].includes(session.status);
  const group = $("group-panel"); group.replaceChildren(); group.hidden = !session.group_scenario;
  if (session.group_scenario) {
    const scenario = session.group_scenario;
    heading(group, scenario.title);
    paragraph(group, "虚构模拟案例，不代表该公司的真实题目。AI 队友均为模拟角色，没有指定领导。");
    paragraph(group, scenario.background); paragraph(group, `讨论任务：${scenario.task}`);
    list(group, "讨论规则", scenario.rules);
    list(group, "AI 队友", scenario.teammates.map(member => `${member.name}：${member.style}`));
    if (session.group_practice_targets) paragraph(group, `本轮练习重点：${session.group_practice_targets.focus}`);
  }
  const conversation = $("conversation"); conversation.replaceChildren();
  session.turns.forEach((turn) => {
    const div = document.createElement("div"); div.className = "turn";
    if (turn.group_round) groupMessages(div, turn.group_round, session.group_scenario);
    paragraph(div, session.mode === "group" ? `讨论任务（第 ${turn.turn_index} 轮）：${turn.question}` : `面试官（第 ${turn.turn_index} 轮）：${turn.question}`);
    if (turn.answer !== null) paragraph(div, `${session.mode === "group" ? "你的发言" : "你的回答"}：${turn.answer}`);
    if (turn.repeat_of) paragraph(div, `这是第 ${turn.repeat_of} 题的重答，之前的回答仍保留。`);
    if (turn.audio_url) {
      const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none"; audio.src = turn.audio_url;
      div.append(audio);
    }
    if (turn.feedback) {
      const feedback = document.createElement("section"); feedback.className = "training-feedback";
      heading(feedback, `第 ${turn.turn_index} 题讲评`);
      paragraph(feedback, turn.feedback.summary);
      [["优点", turn.feedback.strengths], ["改进点", turn.feedback.issues], ["容易被追问的表述", turn.feedback.risks]].forEach(([title, points]) => {
        if (!points.length) return;
        heading(feedback, title, "h4");
        points.forEach((point) => {
          paragraph(feedback, point.observation); paragraph(feedback, `原文证据：“${point.evidence}”`); paragraph(feedback, `建议：${point.suggestion}`);
        });
      });
      list(feedback, "可以这样组织回答", turn.feedback.suggested_structure);
      paragraph(feedback, turn.feedback.timing_advice);
      div.append(feedback);
    }
    conversation.append(div);
  });
  if (session.group_closing) groupMessages(conversation, session.group_closing, session.group_scenario);
  document.querySelector('label[for="answer"]').textContent = session.mode === "group" ? "你的发言（可编辑转写）" : "回答文字（可编辑转写）";
  $("answer-form").hidden = session.status !== "active" || session.training_paused;
  $("voice-answer").hidden = session.status !== "active" || session.needs_next_question || session.training_paused;
  $("training-controls").hidden = !session.training_paused;
  $("training-continue").textContent = session.training_will_end ? "结束专项练习并生成报告" : "继续下一题";
  $("training-repeat").hidden = !session.training_can_repeat;
  $("transcribe-retry").hidden = !session.audio_needs_transcription;
  if (session.status === "active" && !session.needs_next_question && oldIndex !== session.current_turn_index) {
    recordedBlob = null; recordedTurnIndex = null;
    $("voice-status").textContent = "点击开始录音，回答后点击结束录音。";
  }
  if (session.training_paused) {
    $("answer").value = "";
  } else if (session.needs_next_question) {
    $("answer").value = session.turns.at(-1).answer;
    $("answer").readOnly = true;
    $("submit-answer").textContent = session.mode === "training" ? "回答已保存，重试本题讲评" : session.mode === "group" ? "发言已保存，重试队友回应" : "回答已保存，重试生成下一题";
  } else {
    $("answer").readOnly = false;
    $("submit-answer").textContent = session.mode === "training" ? "提交并查看讲评" : session.mode === "group" ? "提交发言" : "提交回答";
    if (oldIndex !== session.current_turn_index) $("answer").value = session.turns.at(-1)?.transcript_raw || "";
  }
  $("finish").hidden = !["active", "completed"].includes(session.status) || Boolean(session.report);
  $("finish").textContent = session.status === "completed" ? "重试生成报告" : "结束面试并生成报告";
  if (session.mode === "group" && session.status === "active") $("finish").textContent = "结束讨论并生成报告";
  const report = $("report"); report.replaceChildren(); report.hidden = !session.report;
  $("upgrade-report").hidden = !session.report || session.report.version >= 3;
  $("practice-again").hidden = !session.can_practice;
  $("practice-again-note").hidden = !session.can_practice;
  $("practice-again").textContent = session.mode === "group" ? "再练一次无领导小组（沿用案例）" : "按本轮计划开始专项练习";
  $("practice-again-note").textContent = session.mode === "group" ? "沿用案例、简历和研究资料，用当前面试模型开启新的讨论，原发言记录保留。" : "沿用本轮简历和已保存的研究资料，用当前面试模型生成新的专项策略和讲评。";
  if (session.report) {
    renderReport(report, session.report, session.turns);
  }
}

async function loadHistory() {
  const result = await api(`/api/interviews?limit=20&offset=${historyOffset}`);
  if (historyOffset >= result.total && historyOffset) { historyOffset = Math.max(0, historyOffset - 20); return loadHistory(); }
  $("history-section").hidden = false;
  $("history-count").textContent = result.total ? `共 ${result.total} 场，按创建时间从新到旧排列。` : "还没有面试记录。";
  $("history-prev").hidden = historyOffset === 0;
  $("history-next").hidden = historyOffset + result.items.length >= result.total;
  const container = $("history-list"); container.replaceChildren();
  result.items.forEach((item) => {
    const row = document.createElement("div"); row.className = "turn"; row.dataset.sessionId = item.id;
    const date = new Date(item.created_at).toLocaleString("zh-CN");
    paragraph(row, `${item.company} · ${item.position} · ${date}`);
    paragraph(row, `${item.interview_type} · ${item.mode === "training" ? "专项练习 · " : item.mode === "group" ? "无领导小组 · " : ""}${statuses[item.status]} · ${item.mode === "group" ? "已发言" : "已答"} ${item.answered_count} ${item.mode === "group" ? "轮" : "题"}${item.has_report ? " · 已有报告" : ""}${item.provider_mode === "demo" ? " · 离线演示" : ""}`);
    const open = document.createElement("button"); open.type = "button"; open.textContent = item.status === "completed" ? "查看问答和报告" : "继续面试";
    open.addEventListener("click", () => work(async () => {
      if (current?.status === "active" && ($("answer").value.trim() || recordedBlob) && !current.needs_next_question) throw new Error("请先完成录音转写、提交或清空当前回答草稿，再切换面试。");
      const next = await api(`/api/interviews/${encodeURIComponent(item.id)}`);
      current = null; recordedBlob = null; recordedTurnIndex = null;
      $("answer").value = "";
      render(next);
      $("history-section").hidden = true;
      $("interview").scrollIntoView();
    }));
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "删除这场面试";
    remove.addEventListener("click", () => work(async () => {
      if (!confirm(`删除 ${item.company} · ${item.position}（${date}）？\n将删除这场问答、报告、资料快照和全部录音；没有被其他场次使用的简历也会删除。准备资料库的独立副本会保留，可在资料库单独删除。无法恢复。`)) return;
      message("正在清理这场面试和录音……");
      await api(`/api/interviews/${encodeURIComponent(item.id)}`, {method: "DELETE"});
      if (current?.id === item.id) {
        current = null; recordedBlob = null; recordedTurnIndex = null;
        $("interview").hidden = true; $("setup").hidden = false; $("answer").value = "";
      }
      if (localStorage.getItem("ai-interviewer-session") === item.id) localStorage.removeItem("ai-interviewer-session");
      const url = new URL(location.href);
      if (url.searchParams.get("session") === item.id) { url.searchParams.delete("session"); history.replaceState(null, "", url); }
      resumeId = null; uploadedFile = null;
      await loadHistory();
      await loadPreparations();
    }));
    row.append(open);
    if (item.preparation_id) {
      const reuse = document.createElement("button"); reuse.type = "button"; reuse.textContent = "复用设置和资料";
      reuse.addEventListener("click", () => work(async () => {
        if (current?.status === "active" && ($("answer").value.trim() || recordedBlob) && !current.needs_next_question) throw new Error("请先提交或清空当前草稿，再开始新面试。");
        const value = await api(`/api/preparations/${encodeURIComponent(item.preparation_id)}`);
        const original = await api(`/api/interviews/${encodeURIComponent(item.id)}`);
        value.settings = original.setup; value.resume = original.resume;
        if ((value.provider_mode === "demo") !== (savedAPISettings.provider === "demo")) throw new Error("这份资料属于另一种模式，请在准备资料库选择当前模式可用的资料。");
        current = null; recordedBlob = null; recordedTurnIndex = null;
        $("interview").hidden = true; $("setup").hidden = false; $("history-section").hidden = true;
        localStorage.removeItem("ai-interviewer-session");
        const url = new URL(location.href); url.searchParams.delete("session"); history.replaceState(null, "", url);
        showPreparation(value); $("setup").scrollIntoView();
      }));
      row.append(reuse);
    }
    row.append(remove); container.append(row);
  });
}

$("show-history").addEventListener("click", () => work(async () => { historyOffset = 0; await loadHistory(); }));
$("close-history").addEventListener("click", () => { $("history-section").hidden = true; });
$("history-prev").addEventListener("click", () => work(async () => { historyOffset = Math.max(0, historyOffset - 20); await loadHistory(); }));
$("history-next").addEventListener("click", () => work(async () => { historyOffset += 20; await loadHistory(); }));
$("upgrade-report").addEventListener("click", () => work(async () => {
  message("正在补充逐题复盘，旧报告会在成功后更新……");
  render(await post(`/api/interviews/${encodeURIComponent(current.id)}/report/upgrade`));
}));

async function prepareAndStart() {
  const id = encodeURIComponent(current.id);
  if (current.status !== "prepared") {
    message(current.mode === "group" ? "正在准备岗位资料和虚构讨论案例……" : current.research_origin === "reused" ? "正在使用本机资料，并按当前简历生成新的面试策略……" : `正在收集 ${current.company} / ${current.position} 面试资料，并生成面试策略……`);
    render(await post(`/api/interviews/${id}/prepare`));
  }
  message(current.mode === "group" ? "正在生成 AI 队友的初始观点……" : "正在生成第一道问题……");
  render(await post(`/api/interviews/${id}/start`));
}

$("practice-mode").addEventListener("change", updateModeFields);
$("practice-again").addEventListener("click", () => work(async () => {
  const sourceId = current.id;
  if (!practiceRequests.has(sourceId)) practiceRequests.set(sourceId, crypto.randomUUID());
  message(current.mode === "group" ? "正在沿用案例与资料，创建新一轮小组讨论……" : "正在沿用本轮简历、资料和计划，创建新的专项练习……");
  render(await post(`/api/interviews/${encodeURIComponent(sourceId)}/practice`, {request_id: practiceRequests.get(sourceId)}));
  practiceRequests.delete(sourceId);
  if (["created", "researched", "prepared"].includes(current.status)) await prepareAndStart();
}));
["continue", "repeat"].forEach((action) => $(action === "continue" ? "training-continue" : "training-repeat").addEventListener("click", () => work(async () => {
  message(action === "repeat" ? "正在准备重答，之前的回答会保留……" : "正在继续专项练习……");
  render(await post(`/api/interviews/${encodeURIComponent(current.id)}/${action}`, {turn_index: current.current_turn_index}));
})));

$("setup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  work(async () => {
    const file = $("resume").files[0];
    const savedResume = $("use-saved-resume").checked ? selectedPreparation?.resume : null;
    if (!file && !savedResume) throw new Error("请选择简历，或沿用以前的简历。");
    if (savedResume) {
      resumeId = savedResume.id; uploadedFile = null;
    } else if (file !== uploadedFile || !resumeId) {
      message("正在上传简历……");
      const form = new FormData(); form.append("file", file);
      const uploaded = await api("/api/resumes", {method: "POST", body: form});
      resumeId = uploaded.id; uploadedFile = file;
    }
    message("正在读取并解析简历……");
    await post(`/api/resumes/${encodeURIComponent(resumeId)}/parse`);
    render(await post("/api/interviews", {
      resume_id: resumeId, company: $("company").value, position: $("position").value,
      interview_type: $("interview-type").value, duration_minutes: Number($("duration").value), notes: $("notes").value,
      preparation_id: $("reuse-research").checked && researchMatchesSettings() ? selectedPreparation.id : null,
      mode: $("practice-mode").value, training_focus: $("practice-mode").value === "training" ? $("training-focus").value.trim() : "",
      group_size: Number($("group-size").value), group_topic: $("group-topic").value.trim(),
    }));
    await prepareAndStart();
  });
});

$("prepare").addEventListener("click", () => work(prepareAndStart));
$("answer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  work(async () => {
    const answer = $("answer").value.trim();
    if (!answer) throw new Error("请先输入回答。");
    message(current.mode === "training" ? "正在保存回答、生成本题讲评和下一题……" : current.mode === "group" ? "正在保存发言并生成队友回应……" : "正在保存回答并决定下一步……");
    render(await post(`/api/interviews/${encodeURIComponent(current.id)}/turn`, {turn_index: current.current_turn_index, answer}));
  });
});
$("finish").addEventListener("click", () => work(async () => {
  // Do not silently discard a draft when ending the interview.
  if (current.status === "active" && !current.needs_next_question && $("answer").value.trim()) {
    throw new Error("输入框里还有未提交的回答，请先提交，或清空后结束面试。");
  }
  message("面试已结束，正在生成逐题复盘报告……");
  render(await post(`/api/interviews/${encodeURIComponent(current.id)}/finish`));
}));
$("new-session").addEventListener("click", () => {
  localStorage.removeItem("ai-interviewer-session"); location.href = "/";
});

function showProviderFields() {
  const provider = $("api-provider").value;
  const values = savedAPISettings.providers[provider];
  $("api-base-url").value = values.base_url;
  $("api-model").value = values.main_model;
  $("api-format").value = values.api_format;
  $("api-key").value = "";
  $("api-clear-key").checked = false;
  $("api-key-status").textContent = values.key_configured ? "本机已保存该服务密钥；留空会保留。" : "该服务尚未保存密钥。";
}

function settingsPayload() {
  return {provider: $("api-provider").value, base_url: $("api-base-url").value.trim(),
    main_model: $("api-model").value.trim(), api_format: $("api-format").value,
    api_key: $("api-key").value.trim(), clear_key: $("api-clear-key").checked};
}

function showVoiceFields() {
  const values = savedVoiceSettings.providers[$("voice-provider").value];
  $("voice-base-url").value = values.base_url;
  $("voice-model").value = values.model;
  $("voice-key").value = ""; $("voice-clear-key").checked = false;
  $("voice-key-status").textContent = values.key_configured ? "语音密钥已保存在本机，不回显。" : "尚未配置语音密钥。";
}

function voicePayload() {
  return {provider: $("voice-provider").value, base_url: $("voice-base-url").value.trim(),
    model: $("voice-model").value.trim(), api_key: $("voice-key").value.trim(), clear_key: $("voice-clear-key").checked};
}

$("voice-provider").addEventListener("change", showVoiceFields);
$("voice-settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  work(async () => {
    savedVoiceSettings = await post("/api/voice-settings", voicePayload());
    showUsagePrices();
    showVoiceFields(); $("voice-settings-result").textContent = "语音设置已保存并生效。";
  });
});
$("voice-test").addEventListener("click", () => work(async () => {
  message("正在检查语音密钥和模型……");
  $("voice-settings-result").textContent = (await post("/api/voice-settings/test", voicePayload())).message;
}));

async function uploadRecording() {
  const index = current.current_turn_index;
  const form = new FormData(); form.append("turn_index", index);
  const id = encodeURIComponent(current.id);
  let result;
  message("正在上传录音并转写，请稍候……");
  if (recordedBlob && recordedTurnIndex === index) {
    const mime = recordedBlob.type.split(";")[0];
    const ext = mime.includes("ogg") ? "ogg" : mime.includes("mp4") ? "m4a" : "webm";
    form.append("file", recordedBlob, `answer.${ext}`); form.append("duration", recordedDuration);
    form.append("recording_id", recordingId);
    result = await api(`/api/interviews/${id}/transcribe`, {method: "POST", body: form});
  } else {
    result = await api(`/api/interviews/${id}/transcribe/retry`, {method: "POST", body: form});
  }
  render(await api(`/api/interviews/${id}`));
  $("answer").value = result.text;
  $("voice-status").textContent = "转写完成，请检查文字后提交回答。";
  recordedBlob = null; recordedTurnIndex = null;
}

$("transcribe-retry").addEventListener("click", () => work(uploadRecording));
$("record-start").addEventListener("click", () => work(async () => {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    throw new Error("当前浏览器不支持录音，请用新版 Edge/Chrome 打开本地程序。");
  }
  if (!savedVoiceSettings.ready) { $("voice-settings").open = true; throw new Error("请先配置语音转写服务。"); }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({audio: true}); }
  catch (error) {
    throw new Error(error.name === "NotAllowedError" ? "麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试。" : "无法使用麦克风，请检查设备连接和其他程序占用。");
  }
  const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported(type));
  try {
    const recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : {});
    const chunks = [];
    recordedBlob = null;
    recordingId = crypto.randomUUID();
    recording = {recorder, stream, started: performance.now(), turnIndex: current.current_turn_index};
    recorder.addEventListener("dataavailable", (event) => { if (event.data.size) chunks.push(event.data); });
    recorder.addEventListener("error", () => {
      stream.getTracks().forEach((track) => track.stop()); recording = null;
      $("record-stop").hidden = true; $("record-start").hidden = false; setBusy(false);
      showError("录音失败，请检查麦克风并重试。");
    });
    recorder.addEventListener("stop", () => {
      if (!recording) return;
      recordedDuration = (performance.now() - recording.started) / 1000;
      recordedTurnIndex = recording.turnIndex;
      recordedBlob = new Blob(chunks, {type: recorder.mimeType});
      stream.getTracks().forEach((track) => track.stop()); recording = null;
      $("record-stop").hidden = true; $("record-start").hidden = false;
      setBusy(false);
      // The raw recording remains in memory if upload fails before reaching the server.
      work(async () => {
        if (recordedDuration < 0.3 || !recordedBlob.size) throw new Error("录音过短，请重新录制。");
        await uploadRecording();
      });
    });
    recorder.start(1000);
    $("record-start").hidden = true; $("record-stop").hidden = false;
    $("voice-status").textContent = "正在录音……回答完毕后点击结束录音。";
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop()); recording = null; throw error;
  }
}));
$("record-stop").addEventListener("click", () => {
  if (recording?.recorder.state === "recording") { $("record-stop").disabled = true; recording.recorder.stop(); }
});
window.addEventListener("beforeunload", (event) => {
  if (recording) { event.preventDefault(); event.returnValue = ""; }
});
window.addEventListener("pagehide", () => recording?.stream.getTracks().forEach((track) => track.stop()));

async function refreshMode() {
  const health = await api("/api/health");
  const names = {demo: "离线演示", deepseek: "DeepSeek", openai: "OpenAI", compatible: "兼容接口"};
  $("mode").textContent = health.ai_mode === "demo"
    ? "当前为离线演示：不联网、不调用 AI，不代表真实面试或评分能力。"
    : `当前为 ${names[health.ai_mode]} · ${health.main_model}。${health.ai_ready ? "" : "尚未配置密钥，请打开 API 设置。"}`;
}

$("api-provider").addEventListener("change", showProviderFields);
$("api-settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  work(async () => {
    message("正在保存本机 API 设置……");
    savedAPISettings = await post("/api/settings", settingsPayload());
    showProviderFields();
    showUsagePrices();
    resumeId = null; uploadedFile = null;
    $("api-settings-result").textContent = "设置已保存并生效。可以测试连接或开始新面试。";
    await refreshMode();
    preparationOffset = 0; await loadPreparations();
  });
});
$("api-test").addEventListener("click", () => work(async () => {
  message("正在测试接口和模型，可能产生少量 API 费用……");
  const result = await post("/api/settings/test", settingsPayload());
  $("api-settings-result").textContent = result.message;
}));

work(async () => {
  savedAPISettings = await api("/api/settings");
  $("api-provider").value = savedAPISettings.provider;
  showProviderFields();
  await refreshMode();
  savedVoiceSettings = await api("/api/voice-settings");
  $("voice-provider").value = savedVoiceSettings.provider;
  showVoiceFields();
  await loadPreparations();
  const id = new URL(location.href).searchParams.get("session") || localStorage.getItem("ai-interviewer-session");
  if (id) {
    try { render(await api(`/api/interviews/${encodeURIComponent(id)}`)); }
    catch (error) {
      // A deleted bookmark must not trap the user on an unrecoverable screen.
      if (!error.message.includes("找不到这条记录")) throw error;
      localStorage.removeItem("ai-interviewer-session");
      const url = new URL(location.href); url.searchParams.delete("session"); history.replaceState(null, "", url);
    }
  }
});
