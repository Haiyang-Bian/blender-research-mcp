# BUG：事务内集合后续变更导致回滚被误判为外部结构冲突

> 2026-09-05 入库追踪：用户已授权保存本报告。对应问题已在 0.17.1 修复，见
> [Collection 回退验收](../validation/2026-09-01-transaction-rollback-collection-guard.md)。
> 下文保留原始观察；历史失败时的恢复提醒不表示当前版本仍需相同处置。

## 摘要

在一个 Blender Research MCP 结构事务中，先创建集合，再通过
`mesh.materialize` 将事务内新建对象放入该集合。随后一次只读的
`mesh.uv.inspect` 因大型网格处理超时而断开连接，事务进入
`conflicted` 状态。调用 `transaction.rollback` 时，插件将同一事务内
“向新建集合加入对象”的后续变化误判为事务外修改，并以
`STRUCTURE_CONFLICT` 拒绝回滚。

结果是：事务既不能回滚，也不能继续；半成品集合和对象留在 Blender
内存中，项目被标记为未保存。

## 环境

- Blender：4.2.23 LTS
- Blender Research MCP add-on：0.17.0
- Protocol：1
- `transactions` capability：13
- 测试文件：`test-model.blend`
- 初始状态：已保存、`is_dirty=false`、无活动事务
- 初始场景代次：9
- 源对象：`绯雪_edit_mesh`
- 源网格规模：98,158 顶点、212,774 边、118,110 面、354,330 loops
- 源网格包含：138 个形态键、4 个 UV 层、761 个顶点组

## 复现步骤

1. 在场景代次 9 开始结构事务：

   - transaction ID：`fcd83057-3c0a-4e80-bbe3-6165045d0190`
   - label：`Modular hair extraction and head surface completion`

2. 在同一事务中依次创建集合：

   - `CODEX_Modular_Character`
   - `CODEX_Modular_Body`
   - `CODEX_Modular_Hair`

3. 调用 `mesh.materialize`，将 `绯雪_edit_mesh` 的
   `SHAPE_KEYS_CURRENT` 状态物化为：

   - 对象：`CODEX_Modular_Body_Object`
   - 目标集合：`CODEX_Modular_Body`
   - `copy.materials=true`
   - `copy.uv=false`
   - `copy.weights=true`

   调用成功，耗时约 13.9 秒，场景代次变为 13。输出对象具有 51 个材质
   槽和 761 个顶点组。

4. 对大型源网格调用只读的 `mesh.uv.inspect`。调用返回：

   ```text
   CONNECTION_LOST: Connection to Blender was lost
   ```

   第二次较小范围的 UV 检查仍在约 10.9 秒后返回相同错误。

5. 重新连接并读取 `project.status`。事务仍存在，但状态已经变成：

   ```text
   conflicted
   ```

6. 在场景代次 13 调用 `transaction.rollback`。返回：

   ```text
   STRUCTURE_CONFLICT: Structural resource changed outside the transaction:
   collection CODEX_Modular_Body
   ```

## 实际结果

- `transaction.rollback` 被拒绝。
- 事务保持 `conflicted`，无法安全继续。
- 以下事务内临时资源仍保留在 Blender 内存中：

  - `CODEX_Modular_Character`
  - `CODEX_Modular_Body`
  - `CODEX_Modular_Hair`
  - `CODEX_Modular_Body_Object`

- `CODEX_Modular_Body` 中仅包含本事务通过 `mesh.materialize` 创建的对象，
  没有证据表明它被用户或事务外调用修改。
- 项目变为 `is_dirty=true`，但这些半成品没有保存到 `.blend`。
- 源对象没有发生拓扑修改；测试前后使用的源网格指纹为：

  ```text
  8b010ff4f4bbd92f14e6024253364a19248c6da0c9a8040af634d76004ec2cf9
  ```

## 期望结果

满足以下任一行为均可接受：

1. 只读检查断开连接不应把没有外部写入的活动事务标记为结构冲突；事务
   应保持可继续状态。
2. 若连接中断触发自动回滚，回滚应按事务增量的逆序移除物化对象，再移除
   事务内创建的集合，并恢复初始场景。
3. 手动调用 `transaction.rollback` 时，应识别
   `CODEX_Modular_Body` 的对象链接变化来自同一事务，而不是报告
   “changed outside the transaction”。

## 初步原因判断

较可能的原因是集合创建增量保存了创建当时的结构指纹；随后同一事务的
`mesh.materialize` 将新对象链接到该集合，使集合结构指纹发生变化。回滚
集合创建增量时，冲突检查没有把后续的事务自有增量纳入所有权链，因而将
合法的事务内变化当成了外部变化。

换言之，这是“同一事务中的后续结构变化”与“真正的外部用户变化”没有被
正确区分，而不是源场景的数据冲突。

## 建议修复

1. 回滚时严格按增量逆序处理：先撤销 `mesh_materialize` 创建的对象及其集合
   链接，再检查和撤销 `collection_create`。
2. 为事务创建的资源维护 transaction-owned 关系；集合的当前结构仅由同一
   事务拥有的对象链接变化造成时，不应触发外部冲突。
3. 或在每次事务内合法结构写入后，更新相关事务资源的期望结构指纹，同时
   保留真正的用户操作修订号作为外部变更证据。
4. 只读请求发生传输超时时，不应无条件污染活动结构事务。应区分：

   - Blender 主线程仍在执行只读计算；
   - 写入请求结果不明；
   - 已确认发生外部结构修改。

5. `mesh.uv.inspect` 对大型多 UV 网格应提供分层懒计算、分块处理或心跳，
   避免约 10 秒后直接表现为 `CONNECTION_LOST`。
6. `STRUCTURE_CONFLICT` 响应中建议增加：期望指纹、当前指纹、最后修改来源、
   对应事务增量 ID，以便判断冲突是否来自事务自身。

## 建议回归测试

### 测试一：事务自有集合与对象

1. 开始事务。
2. 创建父集合和子集合。
3. 在子集合中创建或物化对象。
4. 回滚事务。
5. 断言对象、子集合和父集合全部移除，源场景指纹恢复。

### 测试二：连接中断恢复

1. 开始事务并创建集合及对象。
2. 在只读调用期间模拟传输超时或客户端断开。
3. 重新连接。
4. 断言事务能够确定性地继续或完整回滚，不进入无法处理的
   `conflicted` 状态。

### 测试三：真实外部变化仍受保护

1. 开始事务并创建集合及对象。
2. 用户在 Blender UI 中向该集合加入另一个非事务对象。
3. 调用回滚。
4. 断言插件保留用户对象并报告清晰的外部冲突，不能用本报告建议的容错逻辑
   覆盖真实用户修改。

### 测试四：大型 UV 检查

在约 10 万顶点、35 万 loops、4 个 UV 层的网格上运行
`mesh.uv.inspect`，断言调用能够返回分页结果，或返回明确、不会改变事务
状态的超时错误。

## 当前恢复注意事项

报告生成时 Blender 内仍存在上述未保存的冲突事务和临时资源。不要直接
保存当前场景。安全恢复需要用户明确授权后，以 `save_current=false` 重新
载入最近保存的 `.blend`，或者由插件提供能够正确识别事务自有变化的修复版
回滚操作。
