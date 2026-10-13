// Isolated offline group discussion verification; never runs against real AI settings.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs/promises");

async function main() {
  const baseURL = process.env.INTERVIEW_TEST_URL || "http://127.0.0.1:8003";
  assert.equal((await (await fetch(`${baseURL}/api/health`)).json()).ai_mode, "demo");
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE || undefined});
  const page = await browser.newPage({viewport:{width:1000, height:1000}});
  const errors = []; page.on("pageerror", error=>errors.push(error.message));
  const writes = []; page.on("request", request=>{if(request.method()==="POST") writes.push(new URL(request.url()).pathname);});
  const read = async id=>(await fetch(`${baseURL}/api/interviews/${id}`)).json();
  try {
    await page.goto(baseURL);
    await page.getByText("当前为离线演示：", {exact:false}).waitFor();
    await page.locator("#practice-mode").selectOption("group");
    await page.locator("#group-size").selectOption("6");
    await page.locator("#group-topic").fill("技术团队资源优先级");
    await page.locator("#resume").setInputFiles(path.resolve("examples/sample_resume.docx"));
    await page.locator("#company").fill("小组讨论验收公司");
    await page.locator("#position").fill("技术岗位");
    await page.locator("#duration").selectOption("10");
    await page.getByRole("button", {name:"准备并开始面试", exact:true}).click();
    await page.getByText("讨论任务（第 1 轮）：", {exact:false}).waitFor();
    const sourceId = new URL(page.url()).searchParams.get("session");
    let current = await read(sourceId);
    assert.equal(current.mode, "group");
    assert.equal(current.group_scenario.teammates.length, 5);
    assert.ok((await page.locator("#group-panel").textContent()).includes("虚构模拟案例"));
    assert.equal(await page.locator("#conversation .group-message").count(), 5);
    assert.ok(await page.locator("#training-controls").isHidden());
    await fs.mkdir(path.resolve("data/verification"), {recursive:true});
    await page.locator("#group-panel").screenshot({path:path.resolve("data/verification/group_scenario.png")});
    let summarySeen = false;
    while(current.status === "active") {
      const stage = current.group_stage;
      const answer = stage === "summary"
        ? "总结：优先数据质量与误报治理，培训延后。先小范围验证，保留优先次序的分歧，并明确执行步骤。"
        : "我建议先统一影响与成本标准，再安排数据质量和误报治理。我愿意回应队友不同的优先级意见。";
      if(stage === "summary") summarySeen = true;
      await page.locator("#answer").fill(answer);
      const responsePromise = page.waitForResponse(response=>response.request().method()==="POST" && new URL(response.url()).pathname===`/api/interviews/${sourceId}/turn`);
      await page.getByRole("button", {name:"提交发言", exact:true}).click();
      const response = await responsePromise; assert.equal(response.status(), 200);
      current = await response.json();
      if(current.status === "active") {
        await page.getByText(`讨论任务（第 ${current.current_turn_index} 轮）：`, {exact:false}).waitFor();
        const messages = current.turns.at(-1).group_round.messages;
        assert.equal(messages.length, 2);
        assert.ok(messages.some(message=>message.reply_to_quote && answer.includes(message.reply_to_quote)));
        if(current.current_turn_index === 2) {
          await page.reload();
          await page.getByText("讨论任务（第 2 轮）：", {exact:false}).waitFor();
          await page.locator("#conversation .turn").last().scrollIntoViewIfNeeded();
          await page.screenshot({path:path.resolve("data/verification/group_discussion.png")});
        }
      }
    }
    await page.getByRole("heading", {name:"面试复盘报告", exact:true}).waitFor();
    assert.ok(summarySeen && current.group_closing);
    assert.equal(current.report.group_statistics.user_contributions, 5);
    assert.ok(current.report.group_statistics.user_summary_submitted);
    assert.ok((await page.locator("#report").textContent()).includes("次发言复盘"));
    const original = await read(sourceId); writes.length = 0;
    await page.getByRole("button", {name:"再练一次无领导小组（沿用案例）", exact:true}).click();
    await page.waitForURL(url=>url.searchParams.get("session") !== sourceId);
    await page.getByText("讨论任务（第 1 轮）：", {exact:false}).waitFor();
    const repeatedId = new URL(page.url()).searchParams.get("session");
    const repeated = await read(repeatedId);
    assert.notEqual(repeatedId, sourceId);
    assert.deepEqual(repeated.group_scenario, original.group_scenario);
    assert.equal(repeated.turns.length, 1);
    assert.equal(repeated.turns[0].answer, null);
    assert.ok(!writes.some(url=>url.includes("/resumes") || url.endsWith("/prepare")));
    assert.deepEqual(await read(sourceId), original);
    await page.getByRole("button", {name:"结束讨论并生成报告", exact:true}).click();
    await page.getByRole("heading", {name:"面试复盘报告", exact:true}).waitFor();
    await page.getByRole("button", {name:"历史面试", exact:true}).click();
    await page.locator(`[data-session-id="${sourceId}"]`).getByRole("button", {name:"复用设置和资料"}).click();
    await page.locator("#preparation-detail").waitFor({state:"visible"});
    assert.equal(await page.locator("#practice-mode").inputValue(), "group");
    assert.equal(await page.locator("#group-size").inputValue(), "6");
    assert.equal(await page.locator("#group-topic").inputValue(), "技术团队资源优先级");
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result:"passed", mode:"offline", checked:["group_setup", "fictional_case", "five_ai_peers", "grounded_replies", "reload", "user_summary", "user_only_report", "reuse_case", "history_settings"]}));
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
