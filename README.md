# AI Interview Practice

本地运行的 AI 模拟面试工具。上传简历，选择公司和岗位，完成面试、复盘与专项练习。

不需要注册程序账户；面试模型和语音识别使用自己的 API，记录保存在本机。

## 功能

- **模拟面试**：PDF/DOCX 简历解析、岗位联网研究、依据回答动态追问。
- **语音回答**：录音转写，确认或修改文字后提交。
- **面试复盘**：逐题原文证据、表达建议、知识薄弱点与口语统计。
- **专项练习**：每题讲评、暂停、重答、从报告一键复练。
- **无领导小组**：2～5 位 AI 队友、虚构案例、轮流讨论与同案例复练。
- **资料与历史**：复用准备资料、恢复面试、查看和删除记录。
- **用量统计**：请求次数、token、转写时长、自定义单价估算与预算提醒。

## 快速开始

### Windows EXE（推荐）

1. 在 [Releases](https://github.com/AkaNight/ai-interview-practice/releases/latest) 下载 `ai-interview-practice-v0.1.1-windows-x64.zip`。
2. 解压到自己的文件夹，双击 `AI-Interview-Practice.exe`；无需安装 Python。
3. 程序会打开浏览器，在 API 设置里填写自己的服务和密钥。

运行期间保留控制台窗口；关闭窗口或按 `Ctrl+C` 停止程序。数据保存在 EXE 同目录的 `data/`，更新时保留该目录。重复启动会打开已运行的页面。

### 从源码运行

需要 Python 3.12+。下载源码或克隆仓库后，Windows 双击 `run.bat`，首次启动会创建环境、安装依赖并打开页面。

也可以手动运行：

```bash
python -m venv .venv
# Windows 使用 .venv\Scripts\python；macOS/Linux 使用 .venv/bin/python
.venv\Scripts\python -m pip install -r requirements.lock.txt
.venv\Scripts\python run.py
```

访问地址：<http://127.0.0.1:8000>。端口被其他程序占用时，可使用 `run.bat --port 8001`。

## 配置与使用

默认是**离线演示**，不调用 AI，也不提供真实评价。使用真实面试前，展开首页设置：

| 设置 | 支持的服务 |
| --- | --- |
| 面试模型 | OpenAI、DeepSeek、兼容 Chat Completions / Responses 的接口 |
| 语音转写 | 硅基流动千问 ASR、兼容 `/audio/transcriptions` 的接口 |

保存后，上传简历 → 填写公司和岗位 → 选择模式 → 准备并开始 → 打字或录音回答 → 查看报告。已有准备资料可以复用，省去重复搜索和解析。

简历、回答和录音会发送给所选 API 服务，调用可能收费。密钥和个人数据仅保存在本机，不随发布包提供。用量从启用统计后开始记录；费用按自行填写的单价估算，预算仅提醒，实际消费以平台账单为准。

## 开发与文档

技术栈：Python / FastAPI / SQLite / 原生 HTML、JavaScript。

```bash
.venv\Scripts\python -m pytest
```

测试默认使用离线与模拟接口。AI 朗读、实时抢话暂未支持。

- [详细使用说明](docs/USAGE.md)
- [Windows EXE 构建与验收](docs/WINDOWS_BUILD.md)
- [更新说明](RELEASE_NOTES.md)
