# Blender Research MCP

一个面向长期 Blender 研究的本地、可观察、可回退的语义化 MCP。

本项目不以“让模型执行任意 Blender Python”为主要交互方式，而是提供
选择、聚焦、视口观察、对象变换、材质参数调整和事务回滚等结构化工具。

## 当前状态

项目的首个纵向切片已通过 Blender 4.2.23 LTS 实时烟测：认证传输、
上下文观察、事务化局部缩放、显式/断线回退，以及 Microsoft Store 版
Blender 会话发现。0.4.0 在不依赖窗口像素的 GPU 离屏捕获上增加了诊断
着色、绝对 orbit、捕获绑定的 `viewport.raycast` 和有界 evaluated geometry
摘要。0.4.0 已通过 Blender 被 Codex 完全遮挡时的真实空间诊断烟测，
包括诊断着色、绝对 orbit、正交/透视 raycast、geometry inspect、旧证据
拒绝和事务回退。0.5.1 已实现并真实验证有类型、可回退的对象可见性、
Modifier 状态、Shape Key 值和材质输入预览，包括属性冲突保护、断线自动
回退。0.6.0 新增 `lookdev.compare`，可针对一个已检查属性生成基线和 1–3 个
候选证据，并在每个候选后独立回退与验证。0.7.0 进一步把 Blender 应用启动
与 `.blend` 项目生命周期拆成独立工具：Agent 可启动环境变量配置的 Blender，
随后按用户意图保存、打开、重载或关闭项目。0.7.0 已通过自动化门禁，真实 Blender
冷启动与项目切换验收尚待记录。既有验收记录见
[首个纵向切片](docs/validation/2026-08-28-first-vertical-slice.md) 和
[0.3.1 自主观察闭环](docs/validation/2026-08-29-autonomous-observation.md)，以及
[0.4.0 空间诊断](docs/validation/2026-08-29-spatial-diagnosis.md) 和
[0.5.1 受限 LookDev 写入](docs/validation/2026-08-29-bounded-lookdev-writes.md)。

权威设计与交接信息见 [docs/design.md](docs/design.md)，常见使用流程见
[docs/usage.md](docs/usage.md)，完整文档导航见
[docs/README.md](docs/README.md)。公开仓库位于
[Haiyang-Bian/blender-research-mcp](https://github.com/Haiyang-Bian/blender-research-mcp)。

## 0.6.0 可比较预览

0.6.0 改善评审闭环而不扩大 Blender 写权限。新增
`lookdev.compare`：针对一个已检查的受限属性，自动生成“当前基线 + 1–3 个
绝对候选值”的并列图像、结构化 before/after 和像素差异；每个候选都在独立
事务中应用、捕获并回退，最终必须恢复基线、用户上下文和场景状态。

该工具不会自动选择最佳候选、commit 或保存 `.blend`。用户选定方向后，仍需
通过现有显式事务重新应用。灯光、任意 Modifier 参数、节点拓扑和位置/旋转仍
不进入 0.6.0。详细接口、检查点与验收门槛见
[0.6.0 路线图](docs/roadmap/0.6.0-comparative-previews.md)。

## 0.7.0 应用与项目生命周期

应用和项目是两个独立层次：`application.launch` 只启动或复用 Blender，不接受
项目路径；`project.open` 只操作已经接入 MCP 的 Blender，不会隐式启动应用。
Agent 在用户要求“打开项目”时依次调用 `application.status`、必要时
`application.launch`，最后调用 `project.open`。

- `application.status/launch/quit` 管理 Blender 进程与托管会话；
- `project.status/save/open/reload` 管理当前 `.blend` 文件；
- `project.open` 默认 commit 活动事务、保存 dirty 当前项目，并加载目标保存的
  UI 与受信任项目脚本；
- `project.reload` 默认从磁盘重载并丢弃未保存修改；
- `application.quit` 默认 commit、保存并关闭，显式 `save_current=false` 则直接
  丢弃未保存修改。

用户明确要求保存、切换、重载或关闭即授权相应动作链，不再弹出第二次确认。
所有项目路径必须为绝对 `.blend` 路径，但不受项目根目录白名单限制。托管启动
使用随当前 wheel 发布的固定 add-on/bootstrap，不写 Blender 偏好或 startup
file。完整契约见
[0.7.0 路线图](docs/roadmap/0.7.0-managed-lifecycle.md)。

## 目录

~~~text
blender_addon/            Blender 端插件包
docs/                     设计、决策和协议文档
src/blender_research_mcp/ 外部 MCP 服务端 Python 包
skills/                   仓库版本化的 Codex 工作流 skill
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

检查当前 CLI 版本：

~~~powershell
uv run --no-sync blender-research-mcp --version
~~~

构建 Blender 开发插件：

~~~powershell
uv run --no-sync python scripts/build_addon.py --version 0.7.0
~~~

`--version` 会同时校验项目版本、插件运行时版本和 Blender `bl_info`，并默认
输出对应版本号的 ZIP；任何不一致都会终止构建。统一质量门可以在命令行运行：

~~~powershell
uv run --no-sync python scripts/quality_gate.py
~~~

仓库还提供三个可共享的 PyCharm Run Configuration：

- **Build - Add-on (version)**：启动时询问版本号并执行上述受校验构建；
- **Tests - Pytest**：使用 PyCharm 原生 pytest runner，提供测试树、定位与调试；
- **Tests - Full Quality Gate**：依次执行 pytest、Ruff 和 mypy，首项失败即停止。

配置保存在 `.run/`，pytest runner 明确使用由 `uv sync` 管理的项目 `.venv`，
不会读取或修改个人 `.idea/workspace.xml` 中的旧 SDK 设置。若刚拉取配置后
列表尚未刷新，重新加载项目即可。

外部 MCP 服务无参数时通过 stdio 启动，并自动发现端口 9877 的本地 Blender
插件会话。设置 `BLENDER_RESEARCH_MCP_BLENDER_EXECUTABLE` 后，Agent 也可通过
`application.launch` 启动可见 Blender。服务不提供任意 Python 工具；保存和
切换 `.blend` 只能通过明确的项目生命周期工具完成。

Windows 托管启动应配置一个能直接接收命令行参数的真实 `blender.exe`；Microsoft
Store 的 execution alias 会丢失受管 bootstrap 的环境和参数。已由用户启动的
Store Blender 仍可作为普通现有会话被发现和复用。

## 空间诊断

`viewport.capture` 和 `observation.bundle` 可临时使用 `WIREFRAME`、`SOLID`、
`MATERIAL` 或 `RENDERED`，并在完成后恢复用户的 shading、overlays、选择、
模式和视角。单图捕获还可从一个明确轴向执行绝对 yaw/pitch orbit。

每张成功图片返回会话内 `capture_id`。将图片上以左上角为原点的归一化
`x/y` 传给 `viewport.raycast`，即可获得 evaluated 场景中的几何命中对象、
世界坐标、法线和面索引。场景变化后旧 ID 返回 `CAPTURE_STALE`，调用方必须
重新捕获。`object.geometry.inspect` 提供网格计数、bounds、材质使用、modifier
和有界拓扑摘要，不返回原始网格数组。

## 受限 LookDev 写入

`object.lookdev.inspect` 先枚举对象可见性、Modifier、非 Basis Shape Key 和
材质槽的会话身份；`material.inspect` 再枚举一个精确材质槽内至多 256 个输入
socket，并标明类型、范围、链接、驱动和可写原因。

所有写入都必须位于事务中，并携带检查结果里的精确身份、最新
`scene_generation` 和独立幂等键：

- `object.visibility.set` 只设置 `hide_viewport` / `hide_render`；
- `modifier.set_state` 只设置 `show_viewport` / `show_render`；
- `shape_key.set_value` 只设置非 Basis、无驱动且位于现有 slider 范围内的值；
- `material.set_input` 只设置未链接、无驱动的 Float、Int、Boolean、Vector 或
  Color `default_value`。

共享材质默认拒绝。只有调用者同时提供准确的 `expected_material_users` 和
`allow_shared=true` 才会修改，并返回所有可发现的受影响对象。系统不会自动
复制 single-user 材质，也不会改变节点拓扑。没有明确保留意图时应 rollback；
commit 仅保留 Blender 内存状态，仍不会保存 `.blend`。

当需要比较同一属性的多个绝对值时，`lookdev.compare` 会验证所有身份和实时
基线，再按请求顺序为每个候选执行独立的 begin、单次写入、capture 和 rollback。
只有全部候选都恢复成功时才返回完整图集和差异统计；工具不会给候选排名，也
不会 commit。选定方向后仍应通过普通事务显式应用。

## Blender 控制区域

3D Viewport 的 **Research MCP** N-panel 只显示紧凑状态。完整 endpoint、
heartbeat、scene generation、命令耗时、事务和错误信息位于
**Scene Properties > Blender Research MCP**，并显示当前项目路径、dirty
状态和最近生命周期操作。插件不会自动切割 Area 或创建
Workspace；用户可以手动把任意现有 Area 切换为 Properties Editor。

## 运行条件

`application.status/launch` 与 `project.status/save/open/reload/quit` 不依赖
`VIEW_3D`。视口捕获仍需要 Blender 会话中存在 3D Viewport；GPU 上下文不可用
时返回 `CAPTURE_GPU_UNAVAILABLE`，不会把黑图作为证据。

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
env = { BLENDER_RESEARCH_MCP_BLENDER_EXECUTABLE = "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe" }
startup_timeout_sec = 20
tool_timeout_sec = 60
default_tools_approval_mode = "writes"
~~~

保存后重启 Codex，并用 `/mcp` 检查 `blender_research`。MCP 进程可以在
Blender 未启动时完成初始化；此时 `application.status` 正常返回
`running=false`，`application.launch` 可冷启动 Blender，而 `project.*` 明确
返回 `APPLICATION_NOT_RUNNING`，不会把启动和项目操作隐式耦合。

## 安装常用工作流 Skill

仓库中的 `skills/blender-research-workflow` 定义了连接验证、多视图观察、
项目生命周期、二维到三维诊断、受限写入检查、单变量事务预览、候选比较和恢复流程。安装到个人
Codex skills：

~~~powershell
uv run --no-sync python scripts/install_codex_skill.py
uv run --no-sync python scripts/install_codex_skill.py --check
~~~

安装器只会更新带本仓库来源标记的副本，不会覆盖同名的用户自建 skill。
首次安装后重启 Codex 以启用自动发现。

## 兼容目标

- 首要目标：Blender 4.2.23 LTS；
- Blender 插件代码保持 Python 3.11 语法兼容；
- MCP 服务端开发环境当前使用 Python 3.13；
- 普通安装和 Microsoft Store 打包版 Blender 均支持本地会话发现；
- 通信默认只监听回环地址，不启用遥测和第三方网络集成。
