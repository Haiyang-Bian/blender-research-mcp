# 拓扑失败恢复重建 Vertex Groups，遗留权重守卫导致后续编辑及回滚被误拒绝

> 2026-09-05 入库追踪：用户已授权保存本报告。对应 Group identity 恢复与组件映射问题
> 已在 0.17.2 修复，见[验收记录](../validation/2026-09-03-mesh-recovery-and-join-hotfix.md)
> 及本文末尾复测。下文保留原始失败现场，不覆盖真实用户修改的冲突边界。

- 状态：已观察，待工具侧复现/修复；本次只写报告，不改实现、不提交。
- 日期：2026-09-03；Windows，Blender 4.2.23 LTS，add-on 0.17.1。
- 协议/能力：protocol 1、transactions 13、mesh_topology 5、mesh_component_map 4、mesh_weights 1。
- 严重性：高。普通受控填面失败后，整个已修改事务无法继续编辑或普通回滚；没有尝试强行提交/保存冲突状态。
- 源码检查 HEAD：d55b888883bff84816b2487651fd3d56cfd03143；这只是当前 checkout，不等同于逐字验证运行中的安装包。
- 与现有 mesh.join 原生崩溃报告分开：本次 Blender 未崩溃，不是权限拒绝。

## 已观察的错误链

场景文件仍在独立的 blender-projects 项目，没有复制任何 blend/纹理资产至本仓库。
目标 CODEX_HeadComplete_HeadShell 为本地单用户 Mesh，无 Shape Keys，三层 UV，761 个 Vertex Groups，一个 Armature。
用户原生合入并完成 104 组普通 merge_vertices 后，可靠检查点为 3372 顶点、9638 边、6252 面、18794 面角。

1. 从 generation 146 开始事务 7f29d8fe-e83c-44f0-852f-3626c64f0d95。
2. 完成局部精确合点、耳根小补片、局部翻面、T 接缝细分及两处颔底补片。均采用 PRESERVE_INTERPOLATE；没有调用 weld_vertices。
3. 在 generation 160，用前一步 ComponentMap 映射的边集继续 fill，返回：
   MESH_BOUNDARY_INVALID: Selected edges must be loose or boundary edges
4. 调用失败后 generation 仍为 160，场景计数仍为 3367 顶点、9632 边、6261 面、18821 面角。
5. 重新读取实际边页，重建新的边界 SelectionSet，继续操作即返回：
   MESH_WEIGHT_DATA_CONFLICT: Vertex Group schema changed outside the transaction: CODEX_HeadComplete_HeadShell
6. 用确切事务 ID、generation 160、独立幂等键调用 transaction.rollback，仍返回同一 MESH_WEIGHT_DATA_CONFLICT。
7. project.status 显示事务 active、delta_count 14；context 为 OBJECT，无选择，无用户组名变更证据。
8. 停止写入，不强制覆盖、不另开事务、不用原始 Python 绕过保护。用户执行原生 File > Revert。
9. 恢复后现场 generation 161、active_transaction=null，目标重新为 3372 顶点、6252 面；原生合入及已保存焊接成果保留。

未保存的小补面阶段需重做；自动回滚未成功，不能在日志中记作 rolled_back。
Blender 的 is_dirty=false 在此链上也不能替代事务状态和保存操作证据。

## Vertex Group 证据

完整对比 group.name、group.index、group.lock_weight：761 个组名称、顺序、锁定标记相同。
内部身份参与的 group_schema_fingerprint 却不同：

- 旧：60365cdd1bd0baadbe83d3dc69cf2afa0beea899373fadfcda870e8e81aabc91
- 失败后：bc5aea16a12741a92b85c0c19f61a4382e402f311c5572149b567e384cef18f5

失败后 weights.inspect 报告 5012 条影响项、1906 个未分配顶点。未分配部分是此前已知头皮；
不能据此断言失败没有改变任何权重，因为本报告没有保存该次失败前全部稀疏权重的逐项对照。

## 源码依据及判断边界

在 blender_addon/blender_research_mcp_addon/ 下：

- mesh_topology_ops.py:822–852：异常路径恢复 Mesh，然后调用 _restore_call_state。
- mesh_weight_ops.py:458–467：_restore_call_state 调用 _restore_schemas 再写回捕获权重。
- mesh_weight_ops.py:308–328：_restore_schemas 移除所有现有 Vertex Groups，再按名称/锁定标记重新创建。
- mesh_weight_ops.py:46–58：schema 指纹默认包括 session identity。
- mesh_weight_ops.py:334–363：_validate_weight_guard 比较当前 schema 与 guard.expected_schema_fingerprints。
- mesh_topology_ops.py:871–876：权重守卫指纹只在成功路径更新；异常路径复用已有 weight_guard 时，没有相应更新。

由这些源码可推导：一次错误恢复可以保留语义 schema，却改变所有 group session identities；
仍然持有旧身份的 guard 会把工具自身恢复误判成外部修改。这与现场错误链及指纹变化吻合。
没有额外重放故障，不把这一推导描述为已通过最小回归测试确认。

## 相邻问题：ComponentMap 与刷新后的边界索引不一致（需独立定位）

第一次 MESH_BOUNDARY_INVALID 之前，确实逐步读取了 EDGE/FORWARD ComponentMap，且所需条目均返回单一 target_index。
但随后实际 edge 页显示部分预期边的索引发生了偏移；按映射索引查询时命中了非边界边。
例如当时颔底闭环 [662,3040,1012] 的刷新后边索引为 [8305,5965,7532]，
不是前一版本的 [8304,5964,7531]。

这只能证明本次链路的映射/实际读取不一致；尚未定位是映射生成、Mesh 写回后索引整理，
还是缓存失效时机。不要凭坐标猜 lineage，也不要将这条旁支直接当作权重守卫问题的根因。
建议分别添加：
- 修改后映射 after 指纹与立即 mesh.inspect 的实际指纹一致性；
- EDGE/FORWARD 映射端点经 VERTEX 映射后，与实际目标边端点一致性；
- NGON/TRIANGLES fill 新增对角线后，对所有幸存边做同一检查。

## 建议回归用例（未执行）

1. 创建小型本地 Mesh，至少一个 UV 层和两个非空 Vertex Groups。
2. 开事务，先成功执行一次带权重的拓扑操作，确保已有 weight snapshot guard。
3. 用确切现行版本的合法 SelectionSet 选一条内部边作为 fill 输入，触发可预期的 MESH_BOUNDARY_INVALID。
4. 验证该次调用恢复后几何、UV、组名、锁定和权重与调用前相同。
5. 对同一事务继续一个合法编辑，再分别验证 rollback/commit；不能误报“外部修改”。
6. 覆盖已有 guard、新建 guard、OBJECT 和 SHARED_DATA（合法相同 schema 的用户）路径。
7. 真正由用户改组名、组顺序、锁定或权重时，仍须拒绝覆盖；不能用“全部更新 guard”掩盖真实外部冲突。

候选修复方向是避免在无需变更 schema 的异常恢复中重建组；或在完整验证受控恢复成功后，
精确重绑定由该恢复造成变化的守卫身份。必须同时保留真实用户状态冲突检测。
本报告不授权或实施任何补丁。

## 保存证据与恢复

最后可靠保存：e658141b-590e-4685-8e26-9beb6a4a99d7，generation 146。
项目记录：../blender-projects/notes/head-neck-completion-2026-09-03.md。
操作输入：../blender-projects/configs/head-neck-native-join-seams-20260903.json。
以上 component indices/session identities 仅为历史证据，原生恢复后必须重新检查，不能直接重放。

## 2026-09-03 再次观察：额头 grid_fill 拒绝后 rollback 死锁

用户请求闭合额头裂缝及恢复眉睫。先完成眉睫提取并独立保存到同一 blend，可靠保存操作 `ef0e196e-64e9-4fd4-8064-1b9da23e6b62`，generation 226。原模型不变，磁盘 HeadShell 为 3367 顶点、9630 边、6252 面。

新事务 `7f057277-aafc-4681-8c68-47f89363bb00`：

1. 两次 `merge_vertices TARGET` 对接额头两侧端点；三次 `subdivide(cuts=1,smooth=0)`，全部 PRESERVE_INTERPOLATE。未调用 mesh.join / weld_vertices。
2. 各步刷新 Mesh 指纹，通过 `mesh.selection.remap ALL_MAPPED` 更新上下两链和轨道；版本连续、选区计数分别为 24/24/12。
3. generation 231，selection `2f4d4ea1-8f72-48d9-9562-06d29884e04a` 所代表的 48 边输入 `grid_fill(use_interp_simple=false, material_slot_index=51, smooth=true)` 返回同一个 `MESH_BOUNDARY_INVALID: Selected edges must be loose or boundary edges`。
4. 随即执行一次普通 `transaction.rollback`，幂等键 `forehead-grid-reject-rollback`，被 `MESH_WEIGHT_DATA_CONFLICT: Vertex Group schema changed outside the transaction: CODEX_HeadComplete_HeadShell` 拒绝。
5. project.status 仍 active，delta_count 5、generation 231；is_dirty=false 不能表示已落盘。最后 save operation 仍是眉睫检查点；未强行保存、重载或重试编辑。

异常前最后一次实读、映射 after、异常后实读的 Mesh 指纹均为：
`0564d6c41454c3b014bcf9313c9249dcbc6988fc6f4274c43984b396e57ee832`。
当前计数 3389 顶点、9700 边、6298 面、18932 面角。错误后 schema 指纹 `48eb244f2ac09b02abefb217e0dd859cc85de906819141eeb8e5dbd0cbe7ae55`；本次没有保存错误调用前的完整权重 schema 对照，不复用上一现场指纹作比较。

### 新的映射证据

完整分页读回实边后，48 个所选目标中有 46 个为内部边。例如：

| 基线边 | 基线端点/属性 | 组合映射目标 | 当前端点/属性 |
|---|---|---|---|
| 56 | 992–995，边界 | 56 / 9630，SPLIT | 584–889 / 375–402，均内部 |
| 497 | 2144–2474，边界 | 497 / 9664，SPLIT | 1072–1515 / 567–899，均内部 |
| 2525 | 1002–1003，边界 | 2525 / 9631，SPLIT | 522–740 / 375–488，均内部 |

组合映射 `970137e2-7925-4f2d-bccc-d0b426bd5862` 来自五步连续映射：
`40786efe-e99c-461f-9b6e-650b780abf77` → `e4ef19ca-7b9e-4d8b-bc77-580e65b0b1d2` → `952595e1-8d4a-455b-a01f-04a8ff96b533` → `17609d1d-7e94-40f0-aeec-b8bc6f8f5816` → `63635e49-7459-46c9-bae0-73dffdb0ca87`。

源码只读检查仍为 d55b888883bff84816b2487651fd3d56cfd03143。`mesh_topology_ops.py:715–718` 和 `mesh_ops.py:2198–2238` 在 BMesh 中结束 lineage，然后 to_mesh / update(calc_edges=True)。这是应重点验证的边排序边界，不是已经通过夹具证实的根因。
本次执行也遗漏了既有“映射后逐条核对实边端点/边界”的操作要求；该遗漏应独立记录，不能用工具缺陷推卸验证责任。

新增回归建议：两次 TARGET 合点后，逐次 subdivide 边界链；每一步在**成功返回后立即**对照所有 EDGE lineage 与真实 Mesh 边端点，发现不符就终止，不等到 fill 抛异常。同时检查 subdivide 的 VERTEX lineage 出现全域 DERIVED/CREATED 是否符合语义。

证据在 `../blender-projects/configs/head-eye-rim-forehead-attempt-20260903.json`。当前恢复建议是由用户 File > Revert 回到已保存眉睫检查点；截至本次记录尚未执行。报告不提交，MCP 实现未改。

## 2026-09-03：0.17.2 真实模型复测通过，新增填面限制另记

实时实例 `fb52efeb-4638-43a4-98da-d4b909518da9` 确认加载 add-on 0.17.2；工具源码检查点 `8149533`。先在真实头部独立副本上验证 TARGET 合点、边界细分后的实际边端点，再故意以内部边触发无效 grid_fill，普通回滚成功，副本撤除。原头部几何、三层 UV、组 schema 与权重指纹一致。

随后实际额头阶段两次合点、三次细分均成功，每步全量边映射/实际端点核验通过（9630、9630、9630、9654、9666 条旧边）；没有旧错位问题。最终 grid_fill 因 `Grid fill created no faces` 拒绝，**这次普通 Blender rollback 同样成功，没有 MESH_WEIGHT_DATA_CONFLICT**。权限审核曾先拒绝回滚，但核验仅涉及本阶段 Agent 操作后同路径获准；不可把这个前置拒绝计作 Blender 守卫复发。

本任务涉及的旧 EDGE 映射与失败回滚回归已通过；不宣称覆盖完整发布测试矩阵。当前事务已清除，已恢复 3367 顶点、6252 面的已保存基线，不需要用户 Revert。大额头裂缝仍未补成。

新现象与可能的侧轨分叉条件记录于 [网格填充分诊记录](2026-09-03-grid-fill-branched-boundary-diagnostics.md)，其工具缺陷归属尚未确认。详细证据：`../blender-projects/configs/head-0172-regression-and-grid-fill-20260903.json`。本报告保持未提交。
