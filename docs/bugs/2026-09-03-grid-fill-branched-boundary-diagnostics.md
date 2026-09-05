# 0.17.2：额头两链填面未生成面，侧轨分叉与诊断信息待分诊

> 2026-09-05 入库追踪：用户已授权保存本报告。边界分类、合法分叉、明确四边及补片流程
> 已由 0.17.3–0.17.5 实现，见[验收范围](../validation/2026-09-03-explicit-boundary-patching.md)。
> 该记录不证明原始会话输入在新版本上的逐项重放；下文保留原始现场和不确定性。

## 状态与影响

**一次真实模型复现；根因待判定，不是已确认的工具算法缺陷。** 本例不再出现旧 EDGE lineage 错位或 `MESH_WEIGHT_DATA_CONFLICT`。当前 `grid_fill` 输入在所选子图上为两条等长开链，但未选择的侧轨经过两个全局边界度数为 4 的点。这可能是输入不满足 Blender 原生网格填充的条件，也可能暴露工具预检/文档诊断不足；不能仅凭没有生成面就归咎于工具。

额头大裂缝未闭合。整个未验收额头阶段已成功回滚；头部、UV、权重返回原检查点，无活动事务，不需要用户 Revert。没有修改 MCP 实现、重新执行 Join/Weld，或提交本报告。

## 环境与证据

- Windows；Blender 4.2.23 LTS；实时 `connection.ping.addon_version = 0.17.2`。
- 本次实例 `fb52efeb-4638-43a4-98da-d4b909518da9`，PID 23416。
- 启动 ID `eeedde54-4a8b-40d2-bb1c-50f631765c15`；managed 资源哈希 `c8010825a0f178f553f442307a61f0277fa9f5137e8a2313a801262939f7bb3d`。
- 工具仓库检查点 `8149533`，不是仅根据源码版本判断已部署。
- 原项目：`C:/Users/26687/Work/projects/blender-projects/test-model-fill-checkpoint.blend`。
- 完整输入、逐步映射核验摘要、60 边缺口边界的坐标/端点、恢复结果：`C:/Users/26687/Work/projects/blender-projects/configs/head-0172-regression-and-grid-fill-20260903.json`。不将角色几何复制进工具仓库。

## 已通过的旧问题回归

先在独立复制的真实 HeadShell 上执行 TARGET 合点和一条实际边界边的细分，保留三层 UV 和 761 个顶点组。原边 56 的端点为 `[992,995]`；细分映射 `[56,9630]` 对应实际端点 `[3366,995]`、`[992,3366]`，均仍为边界。

向该副本的一条已确认内部边故意提交无效 grid_fill，正确得到 `MESH_BOUNDARY_INVALID: Selected edges must be loose or boundary edges`；随后普通回滚成功，并撤除了副本。原头部几何、UV、组 schema、权重指纹均未变化。

这只覆盖本任务需要的真实模型操作与恢复；未重新验证 Join、共享 Mesh、注入写回后异常及真实外部修改冲突等完整发布矩阵。

## 本例操作序列

对象 `CODEX_HeadComplete_HeadShell`，Object Mode，起始 3367 顶点、9630 边、6252 面、18794 面角。事务 `85360a87-58ee-46ac-b2d8-1e0a58532e6c`，起始 generation 5。

1. 分别把左右头皮端点 TARGET 合入固定脸部端点。原基线配对为 1268 → 1005、2224 → 1007；第二次操作经过实时 VERTEX 映射与实际坐标核验，不直接重放旧索引。
2. 将额头下边的 12 边逐条二分，上边 18 边中最长的 6 边二分，左右侧轨各 3 边二分。每步保留 UV/weights，使用一跳 ComponentMap 更新 SelectionSet。
3. 五步分别核对 9630、9630、9630、9654、9666 条旧边的映射：实际目标边按 VERTEX lineage 连接正确端点，分裂后为连续路径，旧边界的目标仍为边界；不一致数均为 0。
4. generation 10：3389 顶点、9678 边、6276 面、18866 面角；上/下各 24 边，左/右侧轨各 6 边。四条路径能围成包含 60 个不同顶点的闭环。所选上/下子图各有两个端点，无选区内分叉，所选 48 边均为边界；侧轨存在而未选择。
5. 全局边界图仍在顶点 1005、1007 处各有度数 4。仅检查四条路径分别无分叉不足以发现这一条件，Agent 之前未将全局侧轨可追踪性作为写入前硬门槛。
6. 自交命中面数始终为 83，与阶段起始相同，并非全模型无自交。

## 失败输入与实际输出

```json
{
  "transaction_id": "85360a87-58ee-46ac-b2d8-1e0a58532e6c",
  "expected_scene_generation": 10,
  "expected_mesh_fingerprint": "0d18a76de2fe7a50c024fb12994f7f3066c501ba706c46dafb3f0f50b10f837a",
  "idempotency_key": "forehead172-grid-fill-01",
  "operation": {
    "type": "grid_fill",
    "selection_id": "126d49fa-5868-4db9-b9d8-b334bf7e0227",
    "use_interp_simple": false,
    "material_slot_index": 0,
    "smooth": true,
    "attribute_policy": {"uv": "PRESERVE_INTERPOLATE", "weights": "PRESERVE_INTERPOLATE"}
  }
}
```

准确错误：`Error executing tool mesh.edit: MESH_BOUNDARY_INVALID: Grid fill created no faces`。

该调用前后 Mesh 指纹及 generation 均未变化。调用结束时间 `2026-09-03T06:47:29.084Z`，原始 MCP 记录 ID `exec-0c78407e-79a1-4b90-97a3-e321d88a967b`。

Agent 的结果解包器错误地对纯文本错误调用 `JSON.parse`，先显示了二次 SyntaxError。后续只读检索了本任务日志中的精确幂等键，找回上述原始错误，没有为了取回错误再执行填面。二次解包错误是 Agent 的编排问题，不是 Blender 缺陷。

## 合同、观察与假设

当前 `mesh_topology_ops.py` 的 grid_fill 分支先检查**所选边子图**是一圈或两条路径，再把所选边交给 `bmesh.ops.grid_fill`；没有针对未选侧轨的全局分叉给出专门预检。无新增面时返回通用的 `Grid fill created no faces`。

- 已观察：子图检查通过、48 条实边正确、原生操作没有生成面、两侧轨存在四路边界点。
- 假设：原生算法在分叉侧轨上不能建立唯一路径。这是待验证的拓扑解释，不是已定位根因。
- 不确定：去掉分叉但保留同样几何后是否成功；当前 Blender 版本对原生 grid_fill 的其他隐藏条件；是否还存在绕行或几何排列问题。
- 若该输入本就不兼容：拒绝是正确行为；应考虑将必要条件写入合同，或在预检中指出分叉位置，不要求工具强行填面。

## 恢复与权限审核分层

首次 rollback 在到达 Blender 前被自动权限审核拒绝，理由是可能放弃超过本阶段的成功操作。这不是 Blender 的守卫冲突。

只读核验确认该事务仅包含本轮五个额头准备操作，用户意图版本为 0，没有用户新增修改，先前眼部/眉睫保存不在事务内。提供这些范围证据后，同一 rollback 调用获准并成功，generation 11，`active_transaction = null`，未改走其他执行路径。

恢复核验：

| 项目 | 原基线与回滚后共同值 |
| --- | --- |
| 几何计数 | 3367 顶点 / 9630 边 / 6252 面 / 18794 面角 |
| Mesh 指纹 | `871ab99594fb1aa74f55a0580261ccd073128545b79d47b41a84be67be6ecb3d` |
| UV 指纹 | `edeefe3ac2f981752778a7ee919cf24e4c34b81a6923ed0feb951fc8a62a389c` |
| Group schema 指纹 | `eadb3ea5c90ac4391154febbfdfadc1c067f7e5b08e6e53068f0bb06cd53154d` |
| Weights 指纹 | `b5bf33d09d13ea1d47e63ce0910b0bce2e078b20a1a1ce7e388b0a20b03d2689` |

本轮没有保存新拓扑，磁盘检查点未覆盖。不能把成功恢复说成裂缝补完。

## 后续分诊与模型侧下一步（未实施）

1. 在生成夹具上比较两条等长开链配普通侧轨、带四路分叉侧轨的结果，确定原生限制；不要再用完整人物反复碰撞同一失败输入。
2. 若确认侧轨必须唯一，补充准确的预检/诊断及回归用例；仍保留错误后的属性和事务恢复验证。
3. 模型侧应先建立有宽度的侧连接带、消除点接触分叉，再检查整个缺口的边界图；或用受支持的分区三角填充后细分与固定边界曲面调整。不能只配齐 24/24 边就声称可网格填充。
4. 恢复后的所有索引、选择、Map 都须重新读取；本文件和 JSON 中 ID 只作历史证据。

此报告仅用于分诊，不授权修改工具实现或提交报告。
