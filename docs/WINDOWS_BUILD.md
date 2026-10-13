# Windows EXE

## 使用

下载 Windows x64 发布 ZIP，解压后双击 `AI-Interview-Practice.exe`，无需单独安装 Python。浏览器会打开 `http://127.0.0.1:8000`；保留控制台窗口，关闭窗口或按 Ctrl+C 停止服务。

API 设置、面试和录音放在 EXE 同目录的 `data/`，请放在自己的可写文件夹。升级前先停止程序，替换 EXE，保留 `data/` 和自行配置的 `.env`。源码项目仍可双击 `run.bat`；两种启动方式都使用本地浏览器界面。

重复启动会打开已运行的程序。如果使用其他端口，可以运行：

```powershell
.\AI-Interview-Practice.exe --port 8001
```

迁移源码版资料时，先停止程序，再将原项目的 `data/` 复制到 EXE 同目录。全新下载不含任何用户配置或记录。

## 构建

构建环境：Windows x64、Python 3.14.6、PyInstaller 6.22.3。构建工具与程序运行依赖分开安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_windows.py
.\.venv\Scripts\python.exe scripts/build_release.py
```

使用单文件控制台模式，打包网页静态资源与依赖；不打包 `.env`、`data/`、Git 元数据或开发测试产物。输入源码先通过发布白名单和凭证样式扫描。附 SHA256 校验文件。

网页资源读取打包目录，用户数据读取 EXE 所在目录，避免把数据存进单文件模式的临时解压目录。路径处理参考 [PyInstaller 官方运行时说明](https://pyinstaller.org/en/stable/runtime-information.html)。

## 离线验收

EXE 从隔离目录启动，验证默认演示、无密钥、网页资源、简历解析、动态追问、报告、历史恢复、用量设置保存、重启数据不丢失和重复启动。浏览器检查不调用真实模型、联网搜索或语音 API。

147 项离线自动测试通过。独立 EXE 已验证中文和空格路径、从其他工作目录启动、重启保留记录，以及连接本机模拟 Responses、Chat Completions 和语音接口；未连接付费服务。复现检查可运行 `scripts/exe_smoke.py`，测试记录写入忽略的 `data/exe_verification/`。

本次在 Windows x64 验证；其他 Windows 版本和 ARM64 未单独测试。专项与群面真实模型质量未新增付费验收。
