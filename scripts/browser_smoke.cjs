// Optional development check. Playwright is not a runtime dependency of the app.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");

async function main() {
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8000";
  const health = await (await fetch(`${baseURL}/api/health`)).json();
  assert.equal(health.ai_mode, "demo", "Browser smoke test must use explicit demo mode");
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage({viewport: {width: 1000, height: 1000}});
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const output = path.resolve("data/verification");
  await fs.mkdir(output, {recursive: true});
  try {
    await page.goto(baseURL);
    await page.getByText("当前为离线演示：", {exact: false}).waitFor();
    await page.screenshot({path: path.join(output, "setup.png"), fullPage: true});
    await page.locator("#resume").setInputFiles(path.resolve("examples/sample_resume.docx"));
    await page.getByLabel("目标公司", {exact: true}).fill("中国银行");
    await page.getByLabel("应聘岗位", {exact: true}).fill("信息科技岗");
    await page.getByLabel("面试类型", {exact: true}).selectOption({label: "半结构化面试"});
    await page.getByRole("button", {name: "准备并开始面试", exact: true}).click();
    await page.getByText("面试官（第 1 轮）：", {exact: false}).waitFor();
    await page.locator("#answer").fill("我负责 PCAP 分析，使用 Suricata 规则检测加密流量。");
    await page.getByRole("button", {name: "提交回答", exact: true}).click();
    await page.getByText("你刚才提到 Suricata", {exact: false}).waitFor();
    const sessionURL = page.url();
    assert.ok(sessionURL.includes("?session="));
    await page.reload();
    await page.getByText("你刚才提到 Suricata", {exact: false}).waitFor();
    assert.ok((await page.locator("#conversation").textContent()).includes("我负责 PCAP 分析"));
    await page.getByRole("button", {name: "结束面试并生成报告", exact: true}).click();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    assert.ok((await page.locator("#report").textContent()).includes("1 次文字回答"));
    await page.screenshot({path: path.join(output, "report.png"), fullPage: true});
    await page.reload();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    await page.getByRole("button", {name: "开始新面试", exact: true}).click();
    await page.getByText("当前为离线演示：", {exact: false}).waitFor();
    await page.locator("#resume").setInputFiles(path.resolve("examples/sample_resume.docx"));
    await page.getByLabel("目标公司", {exact: true}).fill("中国银行");
    await page.getByLabel("应聘岗位", {exact: true}).fill("信息科技岗");
    await page.getByRole("button", {name: "准备并开始面试", exact: true}).click();
    await page.getByText("面试官（第 1 轮）：", {exact: false}).waitFor();
    await page.locator("#answer").fill("我们主要使用包长和 IAT 特征。");
    await page.getByRole("button", {name: "提交回答", exact: true}).click();
    await page.getByText("你提到包长或 IAT", {exact: false}).waitFor();
    await page.getByRole("button", {name: "结束面试并生成报告", exact: true}).click();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    assert.ok((await page.locator("#report").textContent()).includes("第 1 题复盘"));
    assert.ok((await page.locator("#report").textContent()).includes("文字回答 1 题"));
    const firstId = new URL(sessionURL).searchParams.get("session");
    await page.getByRole("button", {name: "历史面试", exact: true}).click();
    const firstRow = page.locator(`[data-session-id="${firstId}"]`);
    await firstRow.getByRole("button", {name: "查看问答和报告"}).click();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    assert.ok((await page.locator("#conversation").textContent()).includes("Suricata"));
    await page.getByRole("button", {name: "历史面试", exact: true}).click();
    page.once("dialog", dialog => dialog.dismiss());
    await firstRow.getByRole("button", {name: "删除这场面试"}).click();
    assert.equal((await fetch(`${baseURL}/api/interviews/${firstId}`)).status, 200);
    page.once("dialog", dialog => dialog.accept());
    await firstRow.getByRole("button", {name: "删除这场面试"}).click();
    await page.locator("#setup").waitFor({state: "visible"});
    assert.equal((await fetch(`${baseURL}/api/interviews/${firstId}`)).status, 404);
    assert.equal(await page.evaluate(() => localStorage.getItem("ai-interviewer-session")), null);
    await page.goto(sessionURL); // A deleted bookmark recovers to setup.
    await page.getByText("当前为离线演示：", {exact: false}).waitFor();
    await page.locator("#setup").waitFor({state: "visible"});
    await page.waitForURL(url => !url.searchParams.has("session"));
    assert.ok(!page.url().includes("?session="));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: "passed", sessions: 2, checked: ["upload", "parse", "prepare", "start", "answer", "dynamic_followup", "reload", "finish", "detailed_report", "text_stats", "new_session", "history_open", "delete_cancel", "delete_confirm", "deleted_bookmark"], screenshots: output}));
  } finally {
    await browser.close();
  }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
