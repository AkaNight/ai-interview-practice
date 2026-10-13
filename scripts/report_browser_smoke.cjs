// Read-only browser check against a completed fictional acceptance session.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");

async function main() {
  const id = process.argv[2]; assert.ok(id, "Pass a fictional completed session ID");
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8000";
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage({viewport: {width: 1000, height: 1000}});
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto(`${baseURL}/?session=${encodeURIComponent(id)}`);
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    const text = await page.locator("#report").textContent();
    for (const label of ["第 1 题复盘", "语音回答 1 题", "原始转写", "原文证据", "优先练习建议", "下一轮练习计划", "最长："]) assert.ok(text.includes(label), label);
    await page.locator("#report").scrollIntoViewIfNeeded();
    await fs.mkdir(path.resolve("data/verification"), {recursive: true});
    await page.screenshot({path: path.resolve("data/verification/detailed_report_top.png")});
    await page.getByText("第 1 题复盘", {exact: true}).scrollIntoViewIfNeeded();
    await page.screenshot({path: path.resolve("data/verification/detailed_report_review.png")});
    await page.reload();
    await page.getByRole("heading", {name: "面试复盘报告", exact: true}).waitFor();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result: "passed", checked: ["detailed_report", "raw_transcript", "quote_evidence", "oral_stats", "reload"], session_id: id}));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
