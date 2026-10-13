const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

async function main() {
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8002";
  assert.equal((await (await fetch(`${baseURL}/api/health`)).json()).ai_mode, "demo");
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(baseURL);
    await page.getByText("当前为离线演示：", {exact: false}).waitFor();
    await page.getByText("API 设置（保存在本机）", {exact: true}).click();
    await page.getByLabel("AI 服务", {exact: true}).selectOption("deepseek");
    assert.equal(await page.locator("#api-base-url").inputValue(), "https://api.deepseek.com");
    await page.locator("#api-key").fill("browser-dummy-secret");
    await page.getByRole("button", {name: "保存 API 设置", exact: true}).click();
    await page.getByText("当前为 DeepSeek", {exact: false}).waitFor();
    assert.equal(await page.locator("#api-key").inputValue(), "");
    const safe = await (await fetch(`${baseURL}/api/settings`)).text();
    assert.ok(!safe.includes("browser-dummy-secret"));
    const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
    assert.ok(!storage.includes("browser-dummy-secret"));
    await page.reload();
    await page.getByText("当前为 DeepSeek", {exact: false}).waitFor();
    await page.getByText("API 设置（保存在本机）", {exact: true}).click();
    assert.equal(await page.locator("#api-key").inputValue(), "");
    await page.getByLabel("AI 服务", {exact: true}).selectOption("compatible");
    await page.getByLabel("API Base URL", {exact: true}).fill("https://proxy.example/v1");
    await page.getByLabel("模型名称", {exact: true}).fill("test-model");
    await page.getByLabel("接口类型", {exact: true}).selectOption("responses");
    await page.locator("#api-key").fill("other-dummy-secret");
    await page.getByRole("button", {name: "保存 API 设置", exact: true}).click();
    await page.getByText("当前为 兼容接口", {exact: false}).waitFor();
    assert.equal(await page.locator("#api-key").inputValue(), "");
    await page.getByLabel("AI 服务", {exact: true}).selectOption("deepseek");
    await page.locator("#api-clear-key").check();
    await page.getByRole("button", {name: "保存 API 设置", exact: true}).click();
    await page.getByText("该服务尚未保存密钥。", {exact: true}).waitFor();
    await page.getByLabel("AI 服务", {exact: true}).selectOption("demo");
    await page.getByRole("button", {name: "保存 API 设置", exact: true}).click();
    await page.getByText("当前为离线演示：", {exact: false}).waitFor();
    await page.getByRole("button", {name: "测试连接", exact: true}).click();
    await page.getByText("离线演示可用，没有发出 API 请求。", {exact: true}).waitFor();
    assert.deepEqual(errors, []);
    console.log("Settings browser checks passed: save, switch, reload, no key echo/storage, clear key, draft connection test");
  } finally { await browser.close(); }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
