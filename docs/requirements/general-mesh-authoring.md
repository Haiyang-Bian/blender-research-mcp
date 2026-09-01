# Blender MCP 通用网格建模能力：目标、需求与方案

## 1. 文档目的

本文记录 Blender Research MCP 下一阶段需要解决的核心问题：从当前以观察、LookDev、对象级设置和有限网格编辑为主的工具，发展为能够支持角色模型分割、补面、精细化、重拓扑、属性迁移和形态键处理的通用 Mesh Authoring 接口。

本文讨论的是 MCP 的能力模型，而不是某个角色、眼睛或特定项目的一次性修复脚本。眼白贴合只是用于验证接口是否足够通用的现实案例。

## 2. 背景与目标问题

当前项目已经遇到两类连续的问题：

1. 局部曲面问题：仅靠移动、旋转和缩放椭球，无法让眼白、角膜与非规则眼睑在多个角度下始终贴合。
2. 模型精度问题：现有模型在眼睑、鼻部、嘴唇、脸部轮廓和头发等区域仍显粗糙，后续可能需要大范围分割、补面、细分、重构和属性迁移。

因此，待解决的目标不是增加一个 `fix_eye` 或 `refine_face` 专用工具，而是建立一套通用、原子化、可组合、可验证、可回滚的 Blender 建模语义。

目标包括：

- 精确查询并选择顶点、边、面和连通区域。
- 对单个组件、组件集合和大规模区域执行真实拓扑编辑。
- 支持每个顶点不同的位置、偏移和影响权重。
- 在不同网格和求值状态之间查询、投射和传递几何与属性。
- 允许修改原模型、UV、法线、材质索引、蒙皮权重、形态键和修改器结果。
- 将多个通用操作组合成声明式事务，并在失败时整体回滚。
- 在提交前量化检查缝隙、穿模、非流形、退化面和属性损坏。

## 3. 当前能力基线

2026-08-30 对当前 Blender 4.2.23 LTS、Blender Research MCP add-on 0.11.1 的只读审计结果如下。

### 3.1 已具备能力

- 协议版本 `1`，事务能力 `transactions: 5`，网格能力 `mesh_topology: 1`。
- 能读取稳定的对象、Mesh、Modifier、节点和组件身份。
- 能读取 Mesh fingerprint、拓扑 fingerprint、用户集合和属性层。
- 能按明确索引读取顶点坐标、面中心、法线、面积以及面的顶点/边组成。
- `mesh.edit` 已支持：
  - 组件变换；
  - 面挤出与内插；
  - 边倒角；
  - 删除与溶解；
  - 顶点合并；
  - 面材质/平滑设置；
  - 法线翻转与向外重算。
- 所有写入均受事务、场景代次、对象身份、Mesh 身份、用户集合和 fingerprint 保护。

当前眼白和角膜代理各有 1986 个顶点、4032 条边和 2048 个面；无形态键、单用户，当前接口判定为可写。一个实际位于下眼缘附近的眼白面能够准确追踪到其面索引、四个顶点索引和局部坐标，说明精确组件操作已经具备真实基础。

### 3.2 当前不足

角色本体 `绯雪_edit_mesh` 当前包含约 98158 个顶点、212774 条边和 118110 个面，并带有形态键。0.11.1 将其标记为 `MESH_SHAPE_KEYS_UNSUPPORTED`，因此不能通过 `mesh.edit` 修改。

当前接口还缺少：

- 持久选择集和基于空间、拓扑、材质或屏幕区域的选择查询；
- 一次调用中为不同顶点写入不同坐标；
- 最近点、曲面投射、带偏移 Shrinkwrap 和网格间有符号距离；
- 环切、切割、分离、桥接、填洞、网格细分、三角化/四边形化和重拓扑；
- UV、顶点组、蒙皮权重、形态键位移和自定义法线的写入与迁移；
- 拓扑变化前后组件对应关系；
- 修改器应用、求值网格实体化和通用数据传递；
- 非流形、自相交、退化面、穿模、缝隙和属性完整性检查；
- 多操作声明式批处理。

当前工具能精确修改少量已知组件，但不足以可靠执行大规模模型手术。

## 4. 核心设计原则

### 4.1 面向通用 Blender 语义，而非特定事物

MCP 不应提供 `fix_eye`、`make_face_proxy`、`refine_hair` 等特定业务接口。正确的工具应是 `query`、`select`、`transform`、`project`、`split`、`bridge`、`transfer` 和 `validate` 等通用操作。

“修复眼白”应当是这些通用操作的一种组合：

```text
查询眼白边界
→ 查询求值后的眼睑曲面
→ 将边界投射到目标曲面
→ 向眼眶内部偏移
→ 平滑邻域
→ 检查缝隙与穿透
→ 提交事务
```

### 4.2 原子化不等于一次只操作一个顶点

原子操作的定义应是“一次调用表达一种明确的几何意图，并可独立验证和回滚”。

以下操作都可以是原子操作：

- 将一组顶点投射到一个求值曲面；
- 在一组边上执行一次环切；
- 将一个连通面区域分离为新对象；
- 将一组源权重传递到目标网格；
- 应用一个确定身份的修改器。

不应为了表面上的原子性，迫使调用者逐顶点发送数千次请求。

### 4.3 操作可组合

单个操作保持通用；复杂任务由声明式计划组合。组合层负责：

- 顺序执行；
- 传递前一步的结果和新 fingerprint；
- 更新选择集；
- 在检查点运行验证；
- 发生冲突或验证失败时整体回滚。

### 4.4 一切模型数据均允许修改

原始网格、形态键、UV、蒙皮权重、材质索引、法线和修改器结果都应在能力上允许修改。

“保留原模型”“使用代理”“不应用修改器”只能是工作流策略或默认建议，不能成为 MCP 的永久能力限制。工具应通过显式作用域、处理策略、结构指纹和事务快照控制风险，而不是简单拒绝整个领域。

### 4.5 破坏性操作需要明确语义，而不是禁止

分离对象、应用修改器、删除形态键、改变拓扑和覆盖 UV 都属于合法操作。请求需要声明：

- 精确目标身份；
- 预期结构 fingerprint；
- 数据作用域；
- 拓扑变化策略；
- 是否允许覆盖或丢弃既有数据；
- 失败时的回滚策略。

## 5. 建议的数据模型

### 5.1 ResourceRef

所有对象、Mesh、Modifier、Material、Armature、Shape Key、UV Layer 和 Vertex Group 使用稳定的会话身份与结构 fingerprint 引用，避免仅靠名称定位。

### 5.2 SelectionSet

选择结果应成为一等资源，而不是反复传递裸索引数组。

建议字段：

```json
{
  "selection_id": "...",
  "object_identity": "...",
  "mesh_identity": "...",
  "mesh_revision": 42,
  "domain": "VERTEX | EDGE | FACE",
  "component_count": 128,
  "source_query": {}
}
```

SelectionSet 应支持集合运算、扩张/收缩、边界提取、连通分量和拓扑变化后的重映射。

### 5.3 MeshRevision 与 ComponentMap

每次拓扑变化都产生新的 Mesh revision，并返回旧组件到新组件的映射：

```json
{
  "before_revision": 42,
  "after_revision": 43,
  "vertex_map": {},
  "edge_map": {},
  "face_map": {},
  "deleted_components": [],
  "created_components": []
}
```

后续操作应引用更新后的 SelectionSet，而不是猜测旧索引仍然有效。

### 5.4 坐标空间

所有几何接口明确声明 `LOCAL`、`WORLD`、`NORMAL`、`TARGET_LOCAL` 或 `EVALUATED_WORLD`，禁止隐式坐标转换。

### 5.5 拓扑变化策略

建议统一使用：

```text
REJECT_IF_DEPENDENCIES
UPDATE_DEPENDENCIES
BAKE_CURRENT_STATE
DISCARD_DEPENDENCIES
TRANSFER_TO_NEW_MESH
```

具体请求再声明适用于形态键、UV、权重、法线和修改器的策略。

## 6. 通用原子操作需求

以下名称均为候选接口，而非当前已经存在的工具。

### 6.1 查询与选择

```text
mesh.components.query
selection.create
selection.combine
selection.expand
selection.contract
selection.boundary
selection.connected
selection.shortest_path
selection.inspect
```

查询条件至少包括：

- 显式组件索引；
- 屏幕点、框选和套索；
- 世界/局部空间球体、包围盒和平面；
- 材质槽、法线方向、面面积和边长；
- 连通区域、边界、边环和面环；
- 到另一对象或曲面的距离；
- 非流形、退化、自相交和高曲率区域。

### 6.2 顶点和区域变形

```text
mesh.transform
mesh.vertices.set_positions
mesh.smooth
mesh.relax
mesh.project
mesh.shrinkwrap
mesh.inflate
mesh.flatten
```

需要支持：

- 对 SelectionSet 统一变换；
- 每个顶点不同的绝对坐标或偏移；
- 权重、遮罩和比例衰减；
- 沿法线、指定轴、视图射线或最近点投射；
- 最大距离、表面偏移、正反面过滤和碰撞限制；
- 以求值后的对象作为只读目标。

### 6.3 拓扑编辑

```text
mesh.extrude
mesh.inset
mesh.subdivide
mesh.loop_cut
mesh.bisect
mesh.knife
mesh.bevel
mesh.dissolve
mesh.merge
mesh.split
mesh.separate
mesh.bridge
mesh.fill
mesh.grid_fill
mesh.triangulate
mesh.quadrangulate
mesh.remesh
```

每个拓扑操作必须返回 ComponentMap 和新的完整 fingerprint。

0.13.1 将该长期需求收敛为一个更窄的首版：`mesh.separate` 只接受单个连通、非空、
非全量的 FACE SelectionSet，固定执行对象局部分离，并返回源对象与新对象两条精确
ComponentMap 分支。声明式 `mesh.batch.execute` 仅组合已有 SelectionSet 查询/派生、
Mesh 编辑、该分离操作与有界验证，不开放任意命令或 Blender operator。

实现状态（0.13.1）：上述窄化接口已实现。连续 Map 可通过公开 compose 工具组合；
分离返回 SOURCE/SEPARATED 精确分支；batch 使用一次调用内符号表并在每次拓扑变化
后自动重映射受影响 SelectionSet。对象分离以外的任意对象级批处理、Map 任意图组合、
UV/权重迁移和求值 Mesh 实体化仍未授权。

### 6.4 属性编辑与迁移

```text
mesh.attribute.read
mesh.attribute.write
mesh.attribute.transfer
material.assign_faces
normals.recalculate
normals.transfer
uv.seam
uv.unwrap
uv.pack
weights.read
weights.write
weights.normalize
weights.transfer
shape_key.create
shape_key.delete
shape_key.edit
shape_key.transfer
```

属性传递需要支持最近点、射线、拓扑对应、重心坐标和笼形投射等映射模式。

0.14 将该阶段收敛为 UV 与蒙皮权重两个封闭领域：UV Layer、Seam、Pin、坐标、
ABF/LSCM unwrap、pack、Vertex Group、稀疏权重、归一化、影响数限制，以及拓扑或
最近曲面传递。拓扑与分离默认保留插值，并在无法证明结果完整时回退。带 Shape Key
的 Mesh 可以执行拓扑不变的 UV/权重写入，但 Shape Key、自定义法线和通用属性写入
仍不在本版本授权范围。

实现状态（0.14.0）：上述封闭 UV/权重检查、写入、拓扑/最近映射传递、属性验证、
拓扑迁移策略和 batch 步骤均已实现。Shape Key Mesh 的授权仍只限拓扑不变属性写入。
0.15 优先实现创建独立输出的求值实体化、非连通区域提取和受控 Armature 绑定；Shape
Key 结构写入、自定义法线、通用属性和 Modifier Apply 继续留在后续版本。

### 6.5 对象、求值网格与修改器

```text
mesh.materialize
mesh.extract
rig.inspect
rig.bind
mesh.join.preflight
mesh.join
mesh.edit(weld_vertices)
modifier.apply
```

`mesh.materialize` 从基础 Mesh、仅当前 Shape Key 结果或完整求值结果创建新的独立
Mesh 对象，同时报告丢失或烘焙的依赖。它不修改源对象，也不等同于 Modifier Apply。
面向重新绑定的流程应使用排除 Modifier 的 `SHAPE_KEYS_CURRENT`；完整 EVALUATED 输出
已经包含 Armature 和 Modifier 结果，不能默认再次绑定。

`mesh.extract` 从一个或多个连通片组成的 FACE SelectionSet 创建一个对象，并返回源侧
和提取侧的精确 ComponentMap。`rig.bind` 只装配现有权重与 Armature；权重生成和迁移
继续由独立的权重工具负责。详细要求见
[模块化角色表面需求](modular-character-surface.md)。

0.17 将跨对象合成归入 Mesh 领域，而不是复刻依赖 selection/active-object 的 Blender
Object Join operator。`mesh.join` 创建独立输出并为每个输入返回一条 JOIN_BRANCH
lineage；`weld_vertices` 只合并显式 SelectionSet 与距离规则接受的边界。材质、UV、
权重、颜色、Shape Key、Modifier 和自定义法线均使用明确的合并、丢弃或拒绝策略。

修改器应逐步支持 Shrinkwrap、Mirror、Lattice、Data Transfer、Surface Deform 和 Multiresolution，而不局限于当前的 Bevel、Subdivision、Solidify 和 Boolean。

## 7. 可组合事务

建议增加通用批处理接口，而不是为具体任务增加高级工具：

```json
{
  "transaction_id": "...",
  "operations": [
    {"op": "mesh.components.query", "save_as": "eye_rim"},
    {"op": "mesh.project", "selection": "eye_rim", "target": "..."},
    {"op": "mesh.relax", "selection": "eye_rim", "iterations": 3},
    {"op": "mesh.validate.intersection", "targets": ["...", "..."]}
  ],
  "on_error": "ROLLBACK"
}
```

批处理仍由通用原子操作组成，应具备：

- 最大操作数与几何预算；
- 每一步的身份、revision 和 fingerprint 校验；
- 命名中间结果；
- 选择集自动重映射；
- 显式检查点；
- 完整事务回滚；
- 可序列化的操作清单和执行报告。

## 8. 验证能力

建议提供独立的原子验证工具：

```text
mesh.validate.manifold
mesh.validate.degenerate
mesh.validate.self_intersection
mesh.validate.orientation
mesh.validate.attribute_integrity
mesh.validate.uv
mesh.validate.weights
mesh.validate.shape_keys
mesh.distance
mesh.intersection
```

对于贴合问题，至少需要返回：

- 最小和最大表面距离；
- 最大穿透深度；
- 超过阈值的顶点/边/面；
- 对应世界坐标和法线；
- 可用于视口高亮的 SelectionSet。

对于角色模型，还应验证：

- 极端表情和姿势下的翻面、拉伸和穿模；
- 权重是否归一；
- 形态键拓扑是否一致；
- UV、材质索引和自定义法线是否在拓扑变化后仍有效。

## 9. 建议实施阶段

### 阶段 A：可靠的组件选择与表面拟合

- SelectionSet；
- 空间/拓扑/屏幕查询；
- 批量顶点坐标写入；
- evaluated closest-point；
- project/shrinkwrap；
- distance/intersection 验证。

这一阶段足以可靠处理当前眼白、角膜和眼睑贴合问题。

### 阶段 B：大规模拓扑编辑

- ComponentMap 和 MeshRevision；
- split/separate/bisect/loop-cut/subdivide；
- bridge/fill/grid-fill；
- 可组合批处理事务。

这一阶段开始支持大范围面分割、补面和局部精细化。

### 阶段 C：属性、实体化与绑定

- UV、法线、材质、顶点组和蒙皮权重的读写与传递；
- 当前 Shape Key 结果与最终求值结果实体化；
- 非连通区域提取和 Armature 装配；
- Data Transfer 与 Surface Deform。

这一阶段使修改后的模型能够继续参与角色动画和表情系统。

Shape Key 结构编辑、重映射和迁移是后续独立阶段，不与创建无 Shape Key 工作副本的
materialize 混为同一授权。

### 阶段 D：跨对象合成、Shape Key 与骨架结构

- 多 Mesh 对象 join 与显式接缝 weld；
- Shape Key 结构写入、重映射和迁移；
- Armature 与 Edit Bone 创作；
- 受控 Modifier Apply 与 lineage 证据。

这一阶段按 0.17–0.20 依次实施；每项都必须在上一层的 topology/attribute evidence
通过真实 Blender 验收后再扩大权限。

### 阶段 E：重拓扑与高精度生产

- 通用 remesh/retopology；
- 对称、镜像和拓扑模板；
- Multiresolution；
- 带遮罩的平滑、松弛、膨胀和压平；
- 完整拓扑与动画回归验证。

## 10. 验收标准

MCP 达到目标应至少满足：

1. 能从任意支持的 Mesh 上通过通用查询稳定取得一个组件区域。
2. 能组合完成区域分离、细分、补面、变形和属性传递，不依赖任务专用接口。
3. 拓扑变化后，后续操作不需要猜测新索引。
4. 能显式选择如何处理形态键、UV、权重、法线和修改器依赖，而不是一律拒绝。
5. 能修改原始模型，也能按任务选择复制、代理或求值网格方案。
6. 所有写入都能在事务中完整回滚，并保留用户的视口、选择和工作区操作。
7. 失败时给出结构化冲突或质量报告，不回退到任意 Python、BMesh 或 RNA 执行。
8. 能用几何指标和视口证据共同证明结果，而不只报告“命令执行成功”。

## 11. 总结

下一阶段不应围绕眼睛、脸部或头发增加特定工具，而应建立：

```text
SelectionSet
+ 通用拓扑操作
+ 批量顶点与属性写入
+ 求值曲面查询与投射
+ ComponentMap
+ 可组合事务
+ 几何与角色数据验证
```

这套底座既能处理当前眼球与眼睑贴合，也能支持之后的大型面分割、补充、精细化、重拓扑和角色数据迁移。是否直接修改原模型、创建代理或应用破坏性操作，应由具体任务决定，而不是由 MCP 预先限制。
