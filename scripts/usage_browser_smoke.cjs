// Offline UI checks. Seeded request rows are simulated in the browser only.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

async function main() {
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8003";
  assert.equal((await (await fetch(`${baseURL}/api/health`)).json()).ai_mode, "demo");
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage({viewport:{width:1000,height:1000}});
  const errors = []; page.on("pageerror", error=>errors.push(error.message));
  try {
    await page.goto(baseURL);
    await page.locator("#usage-summary").getByText("0 次请求", {exact:false}).waitFor({state:"attached"});
    await page.locator("#usage-panel summary").click();
    await page.locator("#usage-request-limit").fill("10");
    await page.locator("#usage-budget").fill("5");
    await page.locator("#usage-price-kind").selectOption("voice");
    await page.locator("#usage-audio-price").fill("0.02");
    await page.getByRole("button",{name:"保存提醒和单价",exact:true}).click();
    await page.locator("#usage-settings-result").getByText("提醒和单价已保存",{exact:false}).waitFor();
    let saved = await (await fetch(`${baseURL}/api/usage`)).json();
    assert.equal(saved.settings.monthly_request_limit,10);
    assert.equal(saved.settings.monthly_budget_yuan,5);
    assert.equal(saved.settings.prices[0].audio_per_minute,.02);
    assert.equal(saved.monthly.requests,0);
    await page.reload();
    await page.locator("#usage-summary").getByText("0 次请求",{exact:false}).waitFor({state:"attached"});
    await page.locator("#usage-panel summary").click();
    assert.equal(await page.locator("#usage-budget").inputValue(),"5");
    await page.locator("#usage-price-kind").selectOption("voice");
    assert.equal(await page.locator("#usage-audio-price").inputValue(),"0.02");
    // Rendering known/unknown tokens, warnings, and untrusted model names.
    const date = new Date().toISOString();
    const rows = [
      {id:"fixture-1",created_at:date,operation:"回答与下一轮",kind:"ai",model:"<img src=x onerror=alert(1)>",status:"success",input_tokens:1000,output_tokens:200,total_tokens:1200,audio_seconds:null,estimated_yuan:.014,partial_estimate:true,connection_check:false},
      {id:"fixture-2",created_at:date,operation:"语音转写",kind:"voice",model:"演示 ASR",status:"failed",input_tokens:null,output_tokens:null,total_tokens:null,audio_seconds:60,estimated_yuan:null,partial_estimate:true,connection_check:false}];
    let offsets=[];
    await page.route("**/api/usage?**",async route=>{
      offsets.push(Number(new URL(route.request().url()).searchParams.get("offset")));
      await route.fulfill({json:{...saved,monthly:{...saved.monthly,requests:9,ai_requests:8,voice_requests:1,failed:1,input_tokens:1000,output_tokens:200,total_tokens:1200,unknown_token_requests:1,audio_seconds:60,estimated_yuan:.014,incomplete_cost_requests:2},warnings:["本月请求接近提醒值。","费用未知，请查看平台账单。"],total:31,items:rows}});
    });
    await page.getByRole("button",{name:"刷新用量",exact:true}).click();
    await page.locator("#usage-summary").getByText("9 次请求",{exact:false}).waitFor();
    assert.ok((await page.locator("#usage-warning").textContent()).includes("费用未知"));
    assert.equal(await page.locator("#usage-list img").count(),0);
    assert.ok((await page.locator("#usage-list").textContent()).includes("（部分）"));
    assert.ok((await page.locator("#usage-list").textContent()).includes("未知"));
    await page.getByRole("button",{name:"下一页请求",exact:true}).click();
    await page.getByRole("button",{name:"上一页请求",exact:true}).waitFor();
    assert.ok(offsets.includes(30));
    await fs.mkdir(path.resolve("data/verification"),{recursive:true});
    await page.locator("#usage-panel").screenshot({path:path.resolve("data/verification/usage.png")});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:["settings persist","no demo requests","unknown tokens/costs","warnings","pagination","safe text","mobile width"],paidCalls:0}));
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
