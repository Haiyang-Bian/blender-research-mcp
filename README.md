# Blender Research MCP

一个面向长期 Blender 研究的本地、可观察、可回退的语义化 MCP。

本项目不以“让模型执行任意 Blender Python”为主要交互方式，而是提供
选择、聚焦、视口观察、对象变换、材质参数调整和事务回滚等结构化工具。

## 当前状态

项目已实现并通过首个纵向切片的自动化与 Blender 4.2.23 LTS 实时烟测：
认证传输、上下文观察、视口截图、事务化局部缩放、显式/断线回退，以及
Microsoft Store 版 Blender 会话发现。验收记录见
[docs/validation/2026-08-28-first-vertical-slice.md](docs/validation/2026-08-28-first-vertical-slice.md)。

权威设计与交接信息见 [docs/design.md](docs/design.md)。

## 目录

~~~text
blender_addon/            Blender 端插件包
docs/                     设计、决策和协议文档
src/blender_research_mcp/ 外部 MCP 服务端 Python 包
tests/                    不依赖 Blender 的快速测试
~~~

## 开发

项目使用 uv 管理 Python 与依赖：

~~~powershell
uv sync
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync mypy
~~~

运行当前 CLI 骨架：

~~~powershell
uv run --no-sync blender-research-mcp --version
~~~

构建 Blender 开发插件：

~~~powershell
uv run --no-sync python scripts/build_addon.py
~~~

外部 MCP 服务无参数时通过 stdio 启动，并自动发现端口 9877 的本地
Blender 插件会话。它不提供任意 Python 或保存 `.blend` 文件的工具。

## 配置 Codex

Blender 插件负责监听本地端口；Codex 还需要把外部服务注册为 STDIO MCP。
可在 Codex 的 MCP servers 设置页添加，也可以运行（替换仓库绝对路径）：

~~~powershell
codex mcp add blender_research -- uv --directory C:\absolute\path\to\blender-research-mcp run --no-sync blender-research-mcp
~~~

等价的用户级 `~/.codex/config.toml` 配置如下；`cwd` 必须替换为本仓库
的绝对路径：

~~~toml
[mcp_servers.blender_research]
command = "uv"
args = ["run", "--no-sync", "blender-research-mcp"]
cwd = "C:\\absolute\\path\\to\\blender-research-mcp"
startup_timeout_sec = 20
tool_timeout_sec = 60
default_tools_approval_mode = "writes"
~~~

保存后重启 Codex，并用 `/mcp` 检查 `blender_research`。观察工具可自动调用；
具有修改性的工具会按 annotations 触发写操作审批。MCP 进程可以在 Blender
未启动时完成初始化，但具体工具会返回结构化的连接不可用错误。

## 兼容目标

- 首要目标：Blender 4.2.23 LTS；
- Blender 插件代码保持 Python 3.11 语法兼容；
- MCP 服务端开发环境当前使用 Python 3.13；
- 普通安装和 Microsoft Store 打包版 Blender 均支持本地会话发现；
- 通信默认只监听回环地址，不启用遥测和第三方网络集成。
