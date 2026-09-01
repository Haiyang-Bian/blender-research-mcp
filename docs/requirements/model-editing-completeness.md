# Blender Research MCP：0.16 之后的模型编辑完整性方向

- Status: active direction after the validated 0.16.0 milestone
- Current implementation baseline: 0.17.0 / protocol 1 / transactions 13;
  deterministic live gate passed, aggregate stress and character cage gate pending
- Next implementation milestone: 0.18.0 Shape Key structure and migration

## 1. 目的

0.16 已经闭合“检查或追加模板 → 对齐 → 选区 → 曲面拟合 → UV/权重迁移 →
骨架绑定 → 验证”的模块化角色覆盖面流程。下一阶段不再以某个特定角色或资产为
目标，而是补齐通用模型编辑仍缺失的结构层。

本文中的“完整”不表示复制 Blender 全部 Python API，也不意味着开放任意 BMesh、
RNA、Operator 或脚本。目标是让 Agent 能通过封闭、可检查、可回退的语义工具完成
常见静态模型与可绑定角色模型的工程闭环。

## 2. 当前能力基线

| 领域 | 0.16 状态 | 主要边界 |
|---|---|---|
| 对象与场景 | 已验证 | 创建、复制、删除、TRS、可见性、Collection、父级 |
| 基础 Mesh | 已验证 | 精确组件检查、变形及有界拓扑操作 |
| 语义资源 | 已验证 | SelectionSet、SurfaceRef、ComponentMap、ComponentCatalog |
| 属性 | 已验证 | 材质槽、UV、Seam、Pin、Vertex Group 与权重 |
| 模块化 | 已验证 | materialize、extract、separate、Library append、batch v4 |
| 绑定 | 已验证 | 检查并绑定到现有 Armature |
| 证据与恢复 | 已验证 | 定量验证、视口证据、事务、断连恢复、原生保存接管 |
| 跨对象几何合成 | 0.17 已实现 | 精确 BASE join 与显式 SelectionSet weld；确定性实机门通过，同会话压力与角色笼拼接待关闭 |
| Shape Key 结构 | 未实现 | 只能设置既有值或实体化当前结果 |
| 骨架创作 | 未实现 | 不能创建/编辑骨骼、姿态或动画 |
| Modifier 最终化 | 未实现 | 可编辑四类栈，但不能 Apply |
| 生产级拓扑 | 未实现 | 不含 Sculpt、通用重拓扑、任意 Knife 或 Multiresolution |

## 3. 版本顺序

### 0.17：跨对象 Mesh 合成与接缝焊接

实现状态：代码、schema、事务、自动门禁及确定性 Blender 4.2.23
commit/save/reload 门完成；同会话聚合压力和真实角色笼拼接仍待关闭。

- 将多个精确 Mesh 对象合成为一个独立输出；
- 统一材质槽、UV Layer 与 Vertex Group schema；
- 为每个输入返回通向输出 revision 的精确 ComponentMap 分支；
- 在输出 Mesh 上按 SelectionSet 执行确定性接缝焊接；
- 支持保留输入对象或在 commit 时删除输入对象；
- 将 join、weld 和验证接入声明式 batch。

这是 Shape Key、骨架和 Modifier 最终化之前的基础层：这些后续结构都需要一个稳定、
连续且属性可解释的基础 Mesh。

### 0.18：Shape Key 结构与迁移

- 检查 relative-key 图、slider 范围、mute、vertex group 和驱动状态；
- 创建、复制、重命名、删除及拓扑不变的位置写入；
- 在拓扑相同或有精确 ComponentMap 时迁移 Shape Key；
- 验证 Basis、relative-key 图和极值形态；
- 明确拒绝无法证明的动画、驱动和拓扑迁移。

### 0.19：骨架与骨骼创作

- 创建 Armature 对象和 Armature data；
- 检查、创建、设置、重命名和删除 Edit Bone；
- 设置层级、roll、连接关系及对象级绑定；
- 提供有限姿态验证，但不在首版引入动画曲线或约束图。

### 0.20：受控 Modifier 最终化

- 对当前已支持的 Modifier 类型增加精确 Apply/Convert preflight；
- 在改变拓扑前保存完整 Mesh/属性快照并返回 lineage 能力等级；
- 明确处理 Shape Keys、UV、权重、自定义法线和后续 Modifier 栈；
- Apply 与 `mesh.materialize(FINAL_EVALUATED)` 保持不同语义：前者修改当前对象，
  后者创建独立输出。

### 0.21 及以后：生产级拓扑研究

通用重拓扑、Custom Split Normals、Mirror/Shrinkwrap/Lattice、Multiresolution、
Sculpt 和 Geometry Nodes 需要独立需求与证据模型，不在 0.17–0.20 中暗含实现。

## 4. 共同设计规则

1. 新能力继续使用精确 identity、revision、fingerprint 和 generation。
2. 用户导航、展示、选择和活动对象保持可协作；真实数据漂移仍是冲突。
3. 用户原生保存继续接受当前可见状态并终止后续自动回退。
4. 多对象拓扑操作必须返回每条输入分支的 lineage，不以空间邻近伪造来源。
5. UV、材质、权重、Shape Key、法线和 Modifier 依赖必须显式选择保留、合并、
   丢弃或拒绝，不能静默损坏。
6. 当前场景内的多步建模优先复用 batch 和资源别名，不增加角色专用捷径。
7. 真实验收使用临时 `.blend` 副本，源文件和外部 Library 的 SHA-256 保持不变。

## 5. 完整性判定

完成 0.20 后，可以将该 MCP 描述为“具备常见静态与绑定角色模型的语义编辑闭环”，
但仍不能描述为 Blender 的完整替代接口。只有当跨对象合成、Shape Key、骨架创作和
Modifier 最终化都通过自动门禁、真实 Blender 回归和保存后重载，才应更新这一判断。
