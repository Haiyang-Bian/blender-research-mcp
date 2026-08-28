# Blender Research MCP

一个面向长期 Blender 研究的本地、可观察、可回退的语义化 MCP。

本项目不以“让模型执行任意 Blender Python”为主要交互方式，而是提供
选择、聚焦、视口观察、对象变换、材质参数调整和事务回滚等结构化工具。

## 当前状态

项目已实现首个纵向切片的代码与自动化测试：认证传输、上下文观察、
视口截图、事务化局部缩放与回退。Blender 4.2.23 的实时烟测仍需在 GUI
中完成，因此尚未替换现有 ahujasid/blender-mcp。开发期间两者使用不同
端口并行验证。

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

## 兼容目标

- 首要目标：Blender 4.2.23 LTS；
- Blender 插件代码保持 Python 3.11 语法兼容；
- MCP 服务端开发环境当前使用 Python 3.13；
- 通信默认只监听回环地址，不启用遥测和第三方网络集成。
