// Explicit live check: sends a synthetic microphone recording and fictional resume to configured APIs.
if (!process.argv.includes("--allow-paid")) {
  process.stderr.write("真实语音/面试 API 验收默认关闭。仅在用户明确授权付费测试后使用 --allow-paid。\n");
  process.exit(2);
}
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");

async function main() {
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8000";
  const sample = path.resolve("data/verification/asr_sample.wav");
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined,
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${sample}`]});
  const context = await browser.newContext({permissions: ["microphone"], viewport: {width: 1000, height: 1200}});
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    const health = await (await fetch(`${baseURL}/api/health`)).json();
    assert.ok(health.ai_ready && health.voice_ready);
    await page.goto(baseURL);
    await page.getByText("当前为 兼容接口", {exact: false}).waitFor();
    await page.locator("#resume").setInputFiles(path.resolve("examples/sample_resume.docx"));
    await page.getByLabel("目标公司", {exact: true}).fill("中国银行");
    await page.getByLabel("应聘岗位", {exact: true}).fill("信息科技岗");
    await page.getByRole("button", {name: "准备并开始面试", exact: true}).click();
    await page.getByText("面试官（第 1 轮）：", {exact: false}).waitFor();
    console.log("Interview started", page.url());
    await page.getByRole("button", {name: "开始录音", exact: true}).click();
    await page.getByText("正在录音……", {exact: false}).waitFor();
    assert.ok(await page.getByRole("button", {name: "提交回答", exact: true}).isDisabled());
    // This is an explicit recording duration, not a readiness wait.
    await new Promise(resolve => setTimeout(resolve, 7000));
    await page.getByRole("button", {name: "结束录音并转写", exact: true}).click();
    await page.getByText("转写完成，请检查文字后提交回答。", {exact: true}).waitFor();
    const raw = await page.locator("#answer").inputValue();
    assert.ok(raw.includes("负责") || raw.includes("数据"), `Unexpected sample transcript: ${raw}`);
    console.log("Qwen WebM transcript:", raw);
    const id = new URL(page.url()).searchParams.get("session");
    let saved = await (await fetch(`${baseURL}/api/interviews/${id}`)).json();
    assert.equal(saved.turns[0].answer, null);
    assert.equal(saved.turns[0].transcript_raw, raw);
    assert.ok(saved.turns[0].audio_url);
    assert.equal((await fetch(baseURL + saved.turns[0].audio_url)).status, 200);
    const edited = "我主要负责 PCAP 数据清洗和 Suricata 规则验证，使用包长和 IAT 分析加密流量，然后检查误报。";
    await page.locator("#answer").fill(edited);
    await page.getByRole("button", {name: "提交回答", exact: true}).click();
    await page.getByText("面试官（第 2 轮）：", {exact: false}).waitFor();
    saved = await (await fetch(`${baseURL}/api/interviews/${id}`)).json();
    assert.equal(saved.turns[0].answer, edited);
    assert.equal(saved.turns[0].transcript_raw, raw);
    await page.reload();
    await page.getByText("面试官（第 2 轮）：", {exact: false}).waitFor();
    assert.equal(await page.locator("audio").count(), 1);
    await page.getByRole("button", {name: "结束面试并生成报告", exact: true}).click();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    await page.screenshot({path: path.resolve("data/verification/voice_report.png"), fullPage: true});
    assert.deepEqual(errors, []);
    const result = {result: "passed", session_id: id, audio_format: "webm", transcript_raw: raw, transcript_final: edited, report_generated: true, page_errors: errors};
    await fs.writeFile("data/voice_live_verification.json", JSON.stringify(result, null, 2), "utf8");
    console.log(JSON.stringify(result));
  } finally { await context.close(); await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
