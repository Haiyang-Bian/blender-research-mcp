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
随后按用户意图保存、打开、重载或关闭项目；该闭环已通过真实 Blender 4.2.23
验收。0.8.0 新增从空项目创建对象、Principled 材质、本地图像、World、Camera，
再以 Eevee Next 预览/导出的语义场景创作闭环；自动化门禁和真实月光水面验收
均已通过。0.9.0 新增统一且封闭类型的 `object.set`，可在一次原子调用中设置同一
对象的 TRS、可见性与 Light/Camera 数据；自动化门禁和真实 Blender 4.2.23 验收
均已通过。0.10.0 新增四类有界 Modifier 的完整栈创作：精确检查、创建、类型化
设置、排序、延迟删除和候选比较；自动化门禁和真实 Blender 4.2.23 验收均已通过。
0.10.1 修复同一事务内 linked-data 副本导致 Mesh users guard 自我失效的问题，
并保证复制选中源对象后保存/reload 不会让副本意外进入选择集。
0.10.2 进一步按 Blender RNA 的 float32 实际存储精度比较事务属性，避免
`6.2` 回读为 `6.199999809...` 时误报事务外冲突，同时仍能识别相邻 ULP 修改。
0.11.0 新增基础 Mesh 的分页检查、双指纹和事务 v4 快照，以及统一且封闭的
`mesh.edit` 组件编辑入口；自动化门禁和 Blender 4.2.23 发布验收均已通过。
0.11.1 将事务升级为协作语义 v5：用户可在 Agent 工作期间自由导航视图、切换
Shading/Overlay、选择和活动对象；Blender 原生保存则作为用户接受眼前状态的最终
意图屏障，停止后续写入和回退。
0.12.0 新增 revision-bound SelectionSet、基础/求值 SurfaceRef、定量几何验证，
以及七种保持拓扑不变的选择区域变形；事务升级到 v6，并继续保留用户 UI 与原生
保存优先语义；自动化与 Blender 4.2.23 发布验收均已通过。
0.13.0 新增单 revision `ComponentMap`、跨拓扑 revision 的 SelectionSet 精确重映射，
以及 subdivide、loop cut、bisect、split、bridge、fill 和 grid fill；事务升级到 v7，
所有 lineage 都来自同次 BMesh 操作和精确组件标记，不用空间距离猜测新索引。
0.13.1 在此基础上新增严格连续 ComponentMap 的公开组合、连通 FACE 区域到独立
对象的事务性分离，以及带命名资源、自动 remap 和验证断言的声明式 Mesh batch；
事务升级到 v8，batch 运行期失败会回退整个活动事务，而成功调用只推进一次全局
generation。
0.14.0 新增精确 UV Layer/Seam/Pin/坐标与隔离的官方 unwrap/pack，以及 Vertex
Group schema、稀疏蒙皮权重、属性传递和 UV/权重验证。拓扑与分离现在可显式选择
保留插值、已有属性即拒绝或丢弃结果属性；事务升级到 v9，Shape Key Mesh 也可执行
拓扑不变的 UV/权重写入。
0.15.0 新增显式 BASE / SHAPE_KEYS_CURRENT / FINAL_EVALUATED 实体化、非连通 FACE
区域提取，以及精确 Armature 检查与绑定。三项能力可在同一 transaction-v10 中
组合为独立工作副本、逻辑模块和骨架装配链，不修改、Apply 或替换源对象。
0.15.1 新增 revision-bound ComponentCatalog、精确 Collection 创建/链接、通用对象
父级设置，以及跨对象 `mesh.batch.execute` v3。批处理可将 materialize、连通片目录、
extract、场景组织和 rig.bind 串成一次 transaction-v11 原子装配，并返回带 SHA-256
的会话级 assembly manifest，而不向 `.blend` 写入项目专用元数据。
既有验收记录见
[首个纵向切片](docs/validation/2026-08-28-first-vertical-slice.md) 和
[0.3.1 自主观察闭环](docs/validation/2026-08-29-autonomous-observation.md)，以及
[0.4.0 空间诊断](docs/validation/2026-08-29-spatial-diagnosis.md) 和
[0.5.1 受限 LookDev 写入](docs/validation/2026-08-29-bounded-lookdev-writes.md)，以及
[0.7.0 托管生命周期](docs/validation/2026-08-29-managed-lifecycle.md)，以及
[0.8.0 语义场景创作](docs/validation/2026-08-29-semantic-scene-authoring.md)。
独立比较预览回归见
[0.6.0 比较预览](docs/validation/2026-08-30-comparative-previews.md)，统一对象设置见
[0.9.0 对象设置](docs/validation/2026-08-30-unified-object-settings.md)。
0.10 Modifier 创作见
[0.10.0 验收记录](docs/validation/2026-08-30-modifier-authoring.md)。
linked-data 事务修复见
[0.10.1 验收记录](docs/validation/2026-08-30-linked-data-guard-hotfix.md)。
float32 guard 与独立月光水面实作见
[0.10.2 验收记录](docs/validation/2026-08-30-float32-guard-and-moon-water.md)，语义
Mesh 编辑见
[0.11.0 验收记录](docs/validation/2026-08-30-semantic-mesh-editing.md)。
0.11.1 协作上下文与用户保存优先见
[0.11.1 验收记录](docs/validation/2026-08-30-collaborative-ui-native-save.md)，0.12
SelectionSet 与求值曲面拟合见
[0.12.0 验收记录](docs/validation/2026-08-31-selection-surface-fitting.md)，拓扑 revision
与 ComponentMap 见
[0.13.0 验收记录](docs/validation/2026-08-31-topology-component-maps.md)。
0.13.1 对象分离与声明式 batch 见
[0.13.1 验收记录](docs/validation/2026-08-31-mesh-separation-batches.md)。
0.14 UV 与蒙皮权重见
[0.14.0 路线图](docs/roadmap/0.14.0-uv-and-skin-weights.md)和
[0.14.0 验收记录](docs/validation/2026-08-31-uv-and-skin-weights.md)。0.15 模块化
角色实体化见
[0.15.0 路线图](docs/roadmap/0.15.0-modular-character-materialization.md)和
[0.15.0 验收记录](docs/validation/2026-09-01-modular-character-materialization.md)。
0.15.1 跨对象装配见
[0.15.1 路线图](docs/roadmap/0.15.1-component-catalog-assembly.md)。

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

## 0.8.0 语义场景创作

0.8.0 把事务升级到结构 delta v3，并新增 `scene.inspect`、对象创建/复制/删除与
完整 TRS、标准 Principled 材质、本地图像加载、七类语义贴图通道、World/活动
Camera，以及 Eevee Next 预览和 PNG/EXR 导出。用户明确要求搭建或修改静态场景
时，Agent 可在一个事务内自动完成“发现 → 多步写入 → 预览 → commit”；任何
属性、上下文、结构、链接或用户数冲突都会停止并整批回退。

该版本仍不开放任意 Python、任意节点图、网格组件编辑、Geometry Nodes、动画、
Cycles 或网络资产下载。`transaction.commit` 只保留内存状态；只有明确要求保存
或交付 `.blend` 时才调用 `project.save`。详细契约见
[0.8.0 路线图](docs/roadmap/0.8.0-semantic-scene-authoring.md)。

## 0.9.0 统一对象设置

`object.set` 是同一对象属性的统一公共入口，支持 1–4 个不重复的 transform、
visibility、Light、Camera patch。请求在写入前完成全部校验和事务容量预留，固定
按“变换 → 可见性 → 对象数据”应用，整个调用只推进一次 generation；全为 no-op
时不记录 delta。共享 Light/Camera data 必须携带检查所得 identity、用户数和显式
共享范围。

内部仍按类型分派，不开放任意 RNA。对象创建/复制/删除、活动 Camera、材质、
World、Modifier 和渲染继续使用各自工具。`lookdev.compare` 同时增加
`object_setting` target，可比较变换轴、可见性、灯光颜色/形状/数值和相机参数。
详细契约见
[0.9.0 路线图](docs/roadmap/0.9.0-unified-object-settings.md)。

## 0.10.0 Modifier 创作

`modifier.inspect` 返回精确对象身份、完整有序栈、每项身份与类型化设置，以及
SHA-256 `stack_fingerprint`。`modifier.create/set/move/delete` 只支持 Mesh 上的
Bevel、Subdivision、Solidify 和 Boolean，并在事务中守护完整栈；外部改名、增删、
重排或受保护参数漂移都会停止回退而保留用户状态。

删除在事务内先禁用并标记 `pending_delete`，commit 后才真正移除；rollback 和断线
回退保持原 Modifier identity。Boolean 使用精确 Mesh operand identity，并拒绝
直接/传递环；Subdivision 与 Boolean 具有确定性几何预算。`lookdev.compare` 增加
`modifier_setting` target，但不比较 operand、创建、排序或删除。详细契约见
[0.10.0 路线图](docs/roadmap/0.10.0-modifier-authoring.md)。

## 0.11.0 语义 Mesh 编辑

`mesh.inspect` 分页返回基础 Mesh 的顶点、边或面，外加对象/Mesh identity、完整
对象用户集、预算、`topology_fingerprint` 与包含坐标、材质、平滑、UV/颜色/受支持
属性的 `mesh_fingerprint`。组件索引只在该完整指纹下有效；拓扑修改后必须重新检查。

`mesh.edit` 每次接受一个封闭语义操作：局部组件变换、面挤出/内插、边倒角、
删除/溶解、顶点合并、面材质/平滑或法线处理。`OBJECT` scope 会在共享数据时事务性
单用户化；`SHARED_DATA` 则修改检查所得全部共享对象。事务首次编辑保存完整 Mesh
快照，commit 清理快照，rollback/断线回退在完整 guard 通过后恢复；用户改动返回
冲突并保持原状。

水波等视觉表面细节应优先由材质表达，Modifier 用于非破坏性整体效果，只有真实
轮廓、结构或组件变化才使用 Mesh 编辑。0.11 不开放 UV 修改、任意数组/BMesh、
Modifier Apply、Shape Key 拓扑或任意 Python。详细契约见
[0.11.0 路线图](docs/roadmap/0.11.0-semantic-mesh-editing.md)。

## 0.11.1 人机协作上下文与用户保存优先

事务硬上下文只守护 Scene、View Layer、模式、当前帧和活动 Camera；视图矩阵、
缩放、透视、Viewport lens、Shading、Overlay、选择和活动对象属于用户可协作的 UI
状态。rollback 只恢复事务数据，保留用户最新 UI。比较候选固定使用基线证据矩阵，
因此用户导航不会污染像素差异。

Blender 原生 Ctrl+S、Save As 和 Save Copy 在 `save_pre` 阶段接管活动事务，保留
保存时可见状态并取消断线回退。后续事务或比较请求收到稳定的“已由用户保存接受”
结果后必须停止，不重复保存。MCP `project.save/open/quit` 使用 managed-save 标记，
继续遵循既有先提交再写盘流程。架构依据见
[decision 0011](docs/decisions/0011-collaborative-ui-and-native-save-authority.md)。

## 0.12.0 SelectionSet 与求值曲面拟合

`mesh.inspect` 现在返回由 Blender instance、Mesh identity、完整内容指纹和精确
用户集派生的 `mesh_revision_id`。`mesh.selection.query/derive/inspect/release`
在 add-on 会话内维护有界、不可变的 SelectionSet，不改变 Blender 的真实选择；
局部/世界空间、拓扑、材质、法线、测量和 capture-bound 屏幕查询都绑定该 revision。

`mesh.surface.prepare/query` 可将基础或求值几何固定为带 Scene、View Layer、帧、
对象变换和三角指纹的 SurfaceRef，并提供最近点、射线与距离统计。`mesh.validate`
返回非流形、退化、朝向、相交、距离和穿透证据。`mesh.edit` 新增 set positions、
smooth、relax、project、shrinkwrap、inflate 和 flatten；它们引用 SelectionSet，保持
拓扑不变，并在成功后返回新 revision 及自动重绑定集合。详细契约见
[0.12.0 路线图](docs/roadmap/0.12.0-selection-surface-fitting.md)。

## 0.13.0 拓扑 Revision 与 ComponentMap

`mesh.edit` 的拓扑操作现在返回一步 `ComponentMap`，记录同域组件的
`SURVIVED/SPLIT/MERGED/DERIVED` lineage，以及新建和删除集合。Agent 可通过
`mesh.selection.remap` 把旧 SelectionSet 映射到下一 revision，并通过
`mesh.component_map.inspect` 分页审查正向、反向、新建或删除证据。

新增的封闭操作为 subdivide、quad-ring loop cut、平面 bisect、Mesh 内 split、
双边界 bridge、NGON/triangles fill 和带 rails 的 grid fill。Map 只跨一步；连续拓扑
编辑必须逐步重映射，rollback/断线恢复会使 after-map 失效。详细契约见
[0.13.0 路线图](docs/roadmap/0.13.0-topology-component-maps.md)。

## 0.13.1 对象分离与声明式 Mesh 批处理

`mesh.component_map.compose` 将 2–8 张严格连续 Map 合成为普通 lineage 资源，
仍可分页检查、释放和重映射 SelectionSet。`mesh.separate` 只接受一个连通、非空、
非全量 FACE SelectionSet，并返回 SOURCE/SEPARATED 两条精确分支 Map；共享 Mesh
会先只为目标对象事务性单用户化，peer 不受影响。

`mesh.batch.execute` 在一次 Blender 主线程调用中执行 1–32 个封闭步骤：选择查询/
派生、0.12 变形、0.13 拓扑、对象分离和几何验证。调用内别名替代大规模中间 JSON，
拓扑后自动 remap 当前 SelectionSet，分支链自动生成 composed Map。静态预检失败不
改场景；任一运行期错误或断言失败则回退整个活动事务，包括 batch 前同一事务中的
Agent 写入。详细边界见
[0.13.1 路线图](docs/roadmap/0.13.1-mesh-separation-batches.md)。

## 0.14.0 UV 与蒙皮权重创作

`mesh.uv.inspect/edit` 提供 UV Layer、Seam、Pin、corner 坐标、岛屿变换，以及在
临时对象/私有上下文中运行的 Blender Angle-Based/Conformal unwrap 与 pack；真实
对象的模式、选择、Workspace 和视口不参与算法输入。`mesh.weights.inspect/edit`
提供 Vertex Group 生命周期、精确权重写入、归一化与影响数限制，并明确处理 locked
Group 和共享 Mesh 用户的 schema 一致性。

`mesh.attribute.transfer` 支持 topology lineage、nearest vertex 和 barycentric
nearest surface 的 UV/权重迁移；`mesh.validate` 返回 UV 越界、退化、重叠、stretch
及权重总和、影响数、未赋权和骨骼匹配问题的 SelectionSet。`mesh.batch.execute`
可在同一次主线程调用中组合这些步骤，并在 UV revision 改变后自动重绑定当前目标的
SelectionSet。详细边界见
[0.14.0 路线图](docs/roadmap/0.14.0-uv-and-skin-weights.md)。

## 0.15.0 模块化角色实体化与绑定

`mesh.materialize` 从精确基础 Mesh、仅当前 Shape Key 结果或实时最终求值结果创建
独立对象。输出没有 Shape Key、Modifier 或父级，保持源对象世界变换，并逐项声明
材料、UV 与权重复制；拓扑一致时返回精确 `MATERIALIZATION` ComponentMap，拓扑
变化时明确不猜测 lineage。

`mesh.extract.preflight/extract` 将一个或多个连通 FACE 区域提取为单一对象，同时
返回 SOURCE/EXTRACTED 两条分支 Map、两侧 SelectionSet 和属性迁移证据。
`rig.inspect/bind` 只装配已有权重：精确验证 Armature、骨骼 schema、Group 与现有
Modifier，创建或更新一个 Armature Modifier，并按显式策略设置对象父级；不会隐式
生成、传递或归一权重。

三步可在同一事务中串联，任一步失败、显式 rollback 或断连都会恢复到 begin
基线；Blender 原生保存仍以用户当前可见状态为最终接管。0.15 不开放 Shape Key
结构写入、Modifier Apply、任意 RNA/BMesh/Python、Library append 或角色专用捷径。
详细契约见
[0.15.0 路线图](docs/roadmap/0.15.0-modular-character-materialization.md)。

## 0.15.1 ComponentCatalog 与跨对象装配

`mesh.component_catalog.prepare/inspect/select/release` 将一个实时 FACE
SelectionSet 按共享边划分为紧凑、可分页的连通片目录；只有选中的组件才物化为新
SelectionSet。Catalog 与对象、Mesh、用户集、revision 和完整指纹绑定，不受用户
视图或真实选择影响，Mesh 改变后会明确 stale。

`collection.inspect/create/link_object/unlink_object` 和
`object.parent.set/clear` 提供精确、可回退的场景组织。移动对象需先链接目标
Collection 再取消旧链接；最后一个 Collection 链接不会被隐式移除。父级操作显式
选择 KEEP_WORLD 或 KEEP_LOCAL，并拒绝父级循环。

`mesh.batch.execute` v3 可用别名在一次主线程调用中编排 materialize、Catalog、
extract、Collection、父级和 rig.bind。运行期失败回退完整活动事务，成功写入只推进
一次 generation；响应中的 `assembly_manifest` 汇总最终资源和证据并计算 SHA-256，
但不持久写入项目属性。详细边界见
[0.15.1 路线图](docs/roadmap/0.15.1-component-catalog-assembly.md)。

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
uv run --no-sync python scripts/build_addon.py --version 0.15.1
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
和有界求值后拓扑摘要。`mesh.inspect` 则分页返回受完整指纹约束的基础 Mesh
组件；两者不能互换。

## 受限 LookDev 写入

`object.lookdev.inspect` 先枚举对象可见性、Modifier、非 Basis Shape Key 和
材质槽的会话身份；`material.inspect` 再枚举一个精确材质槽内至多 256 个输入
socket，并标明类型、范围、链接、驱动和可写原因。

所有写入都必须位于事务中，并携带检查结果里的精确身份、最新
`scene_generation` 和独立幂等键：

- `object.set` 统一设置同一对象的 TRS、可见性与有类型的 Light/Camera 数据；
- `object.visibility.set` 只设置 `hide_viewport` / `hide_render`；
- `modifier.set_state` 只设置 `show_viewport` / `show_render`；
- `modifier.create/set/move/delete` 管理四类受支持 Modifier 的类型化有序栈；
- `mesh.edit` 在事务 v4 中修改精确基础 Mesh 组件，并显式选择对象或共享数据范围；
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

0.9 的 `object_setting` 比较 locator 复用 `object.set`，支持单个 transform axis、
visibility、Light 或 Camera 字段。十六进制灯光颜色会在线性 RGB 中验证恢复，
但报告保留调用者提交的原始 JSON 值。

0.10 的 `modifier_setting` locator 携带完整对象、Modifier、类型、索引和栈指纹，
复用 `modifier.set` 比较一个数值、整数、布尔或枚举字段。创建、删除、排序和
Boolean operand 仍只通过显式事务操作，不进入候选比较。

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
项目生命周期、二维到三维诊断、意图驱动场景创作、受限写入检查、单变量事务预览、候选比较和恢复流程。安装到个人
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
