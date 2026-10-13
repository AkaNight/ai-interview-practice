# v0.1.1

2026-10-13

- 新增 Windows x64 EXE，无需单独安装 Python；解压后双击运行。
- 数据与 API 配置保存在 EXE 同目录的 `data/`，重启和替换 EXE 后可继续使用。
- 重复启动自动打开已经运行的页面。
- 精简 README，将详细操作与技术说明移入独立文档。

下载 `ai-interview-practice-v0.1.1-windows-x64.zip`，解压后双击 `AI-Interview-Practice.exe`。源码版使用 `ai-interview-practice-v0.1.1.zip`，需要 Python 3.12+，Windows 双击 `run.bat`。

默认离线演示。真实面试和语音转写需配置自己的 API；发布包不包含密钥或个人记录。运行期间保留控制台窗口，关闭窗口或按 Ctrl+C 停止程序。升级保留 `data/` 和自己创建的 `.env`。

本次打包与功能验收使用离线模式，没有付费 API 调用。专项练习和群面的真实模型质量仍未做额外付费验收。
