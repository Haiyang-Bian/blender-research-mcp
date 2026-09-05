# mesh.join：空材质槽映射异常，以及补齐材质后的 Blender 原生崩溃

> 2026-09-05 入库追踪：用户已授权保存本报告。对应空材质槽与 UV/Pin 原生写入问题
> 已在 0.17.2 修复并完成所记录的回归，见[修复验收](../validation/2026-09-03-mesh-recovery-and-join-hotfix.md)。
> 下文保留原始观察，不将当时的“未提交/待修复”措辞作为当前状态。

- 状态：待定位/修复；本报告未修改实现，未做提交。
- 严重性：高。第二次调用导致 Blender 进程退出，内存中未保存的修改丢失。
- 发生时间：2026-09-02，原生崩溃约 23:33:53（Asia/Shanghai）。
- 平台：Windows；Blender 4.2.23 LTS；加载的 add-on 版本 0.17.1。
- 协议/能力：protocol 1、transactions 13、mesh_join 1、mesh_component_map 4。
- 涉及入口：`mesh.join.preflight`、`mesh.join`。
- 本次只读检查的工具仓库 HEAD：`d55b888883bff84816b2487651fd3d56cfd03143`。
  该值是检查时的 checkout，不等于已证明崩溃进程加载了完全相同的源码。

## 概要

在人物脸壳与独立内缩头皮的合并任务中，先后出现两类失败：

1. 一侧有材质槽，另一侧没有材质槽：preflight 接受输入，但 join 在
   `create_output` 阶段返回 `MESH_JOIN_FAILED / KeyError`。普通事务回滚成功。
2. 给无槽对象追加一个已有皮肤材质，提交该材质事务，重新检查输入后再次
   join：连接丢失，Blender 原生崩溃，日志报告 `EXCEPTION_ACCESS_VIOLATION`。

第一类已有明确源码依据；第二类尚无可用原生堆栈，不能宣称与第一类同因，
也不能宣称是权限、显存、UV 或某一 Blender API 已被确定导致。

## 业务场景与输入

模型位于独立场景项目：
`C:/Users/26687/Work/projects/blender-projects/test-model-fill-checkpoint.blend`。
未将模型、纹理或渲染资产复制到本工具仓库。

| 合并源 | 顶点 | 边 | 面 | 面角 | UV | 顶点组 | Modifier |
|---|---:|---:|---:|---:|---|---:|---|
| `CODEX_HeadComplete_HeadShell` | 1027 | 2928 | 1899 | 5700 | UVMap / UV1 / UV2 | 761 | 1 个 Armature |
| `CODEX_HairEnvelope_HeadCandidate` | 1906 | 5493 | 3588 | 10764 | 无 | 0 | 无 |

两者均为可写本地 Mesh、单一数据用户、无 Shape Keys，位置 `[1.6, 0, 0]`，
旋转为零、缩放为一。脸壳保留原骨架父关系，头皮没有父对象。
脸壳有 51 个材质槽（存在重复材质）；第一次头皮材质槽为空，第二次头皮
槽 0 为已存在的 `CODEX_Modular_Scalp_Skin`。

脸壳和头皮都带有预期的开放边界；合并目的是先组合为一个独立 Mesh，
不是进行 Boolean、封闭体推断或自动焊接。

## 请求语义

每次均重新取得 source Object/Mesh identity、结构指纹、Mesh revision/full
fingerprint、UV/group/weight/Shape-Key fingerprints、Modifier stack fingerprint
和目标 Collection identity/fingerprint。以下只列不随会话失效的策略部分：

```json
{
  "output": {
    "new_object_name": "CODEX_HeadComplete_Fitted",
    "new_mesh_name": "CODEX_HeadComplete_FittedMesh",
    "collection_name": "CODEX_Modular_Body",
    "coordinate_frame": {
      "type": "SOURCE_OBJECT",
      "source_object_name": "CODEX_HeadComplete_HeadShell"
    },
    "source_disposition": "KEEP"
  },
  "attributes": {
    "materials": "PRESERVE_BY_IDENTITY",
    "uv": "MERGE_BY_NAME",
    "weights": "MERGE_BY_NAME",
    "colors": "MERGE_BY_NAME",
    "generic": "ERROR_IF_PRESENT",
    "custom_normals": "DROP_RECALCULATE"
  },
  "dependencies": {
    "shape_keys": "ERROR_IF_PRESENT",
    "modifiers": "DROP_OUTPUT"
  }
}
```

这是策略摘要，不是可直接重放的完整请求；身份/资源 ID 必须来自新会话的
实时检查。源上附带 FACE/边界 SelectionSet，以生成 JOIN_BRANCH 映射供后续
接缝操作使用。不得把旧会话 ID 当作复现凭据直接发送。

保存的第一次 preflight 结果为 `status=ready`、`warnings=[]`、generation 15，
预期输出 2933 顶点、8421 边、5487 面、16464 面角。

## 失败 A：空材质槽并未被正确纳入映射

### 复现步骤

1. 准备两个带面的本地 Mesh：A 至少有一个材质槽，B 没有任何材质槽。
2. 以 `materials=PRESERVE_BY_IDENTITY` 做 preflight。
3. 开事务，用同一组已检查证据执行 join。

实际复杂场景已发生该问题；上述最小夹具是建议添加的回归用例，尚未独立运行。

### 实际与预期

实际：`MESH_JOIN_FAILED`，`create_output` 阶段出现 `KeyError`；该次事务
`afa1caa6-f53b-4b02-b994-ed7ab89370e9` 的 `delta_count=0`，正常回滚成功。

预期：明确支持无材质面的映射，或在 preflight 返回有解释的验证错误；
不能先报告 ready 再因缺失字典键失败。

### 源码依据

`blender_addon/blender_research_mcp_addon/mesh_join_ops.py`：

- `_material_schema()`（检查时约 255–298 行）只枚举已有槽；空槽列表不会
  向 `indices` 添加表示无材质面的 `None`。
- `_build_output()`（约 649–661 行）遇到 B 的 polygon 时将
  `source_material` 和 `identity` 设为 `None`，随后查询
  `schemas["materials"]["indices"][identity]`，触发缺键。
- 槽列表为空与槽列表中包含显式空槽是不同的输入，应分开测试。

## 失败 B：补齐材质后原生崩溃

### 操作顺序

1. 已把内缩头皮几何保存到原检查点。
2. 失败 A 回滚后，单独开材质事务，为头皮追加已有皮肤材质；目视检查，
   提交内存事务。此材质赋值尚未保存到磁盘。
3. 刷新源证据并再次通过 preflight，开启独立合并事务。
4. 执行相同合并策略；MCP 返回 `CONNECTION_LOST`，Blender 进程退出。

合并事务 ID：`2e2d74e2-c428-4c52-b8a3-bdeae4494f58`，开始 generation 16。
join idempotency key：`76569a6c-6f6d-4588-a4fd-9d60b7c38c59`。

### 原始日志证据

```text
Error   : EXCEPTION_ACCESS_VIOLATION
Address : 0x00007FFDCC70DF8F
Module  : ntdll.dll
Thread  : 00007f34
Read blend: "C:\Users\26687\Work\projects\blender-projects\test-model-fill-checkpoint.blend"
Writing: C:\Users\26687\AppData\Local\Temp\test-model-fill-checkpoint.crash.txt
```

日志文件：
`C:/Users/26687/AppData/Local/blender-research-mcp/managed/0.17.1/44b472c05ba070d194fc1d13d5f861f6453832856cc28ead34d3c00376c84822/logs/launch-30654bec-db98-42df-a77c-ec30df8aba8f.log`。

检查时 `test-model-fill-checkpoint.crash.txt` 长度为 **0 字节**；没有可据以
定位具体原生函数的堆栈。`ntdll.dll` 是异常报告模块，不是根因结论。

### 恢复与影响

- 未尝试在崩溃进程中强制回滚；重启并打开同一已保存检查点。
- 候选头皮的完整 Mesh fingerprint 与崩溃前已保存版本一致，几何保住了。
- 未保存的材质赋值丢失，随后在单独事务恢复、检查并保存。
- 未产生成功的 `CODEX_HeadComplete_Fitted` 输出，接缝工作中断。
- 后续用户使用 Blender 原生对象合并成功。只读检查确认当前对象为
  2933 顶点、8421 边、5487 面、16464 面角，仍有两个几何连通片。
  这说明可绕过语义 join 完成对象组合，但不证明语义路径与原生路径完全等价。
- 当前 `.blend` 已被用户合并并保存，不再是原始两对象复现夹具；不要直接
  复用旧对象名/指纹。`.blend1` 也会滚动更新，不能默认仍是失败前版本。

## 定位建议与验收条件（尚未执行）

1. 分离复现 A 和 B。先补齐空槽场景的最小回归，再对原生崩溃建独立夹具。
2. 对网格构造、edge/material 属性写入、UV 分配、权重复制、结构守卫和
   ComponentMap 建立记录可落盘阶段标记；native crash 不会进入 Python except。
3. 分别验证 geometry-only、加材质、加一层/多层 UV、加顶点组及源 Modifier
   丢弃策略。定位顺序不是根因认定，不能声称丢弃 UV 就是已验证修复。
4. 覆盖零面积面/重合顶点等非理想输入。当前脸壳中查到一个零面积四边形；
   是否与崩溃有关未知。即使拒绝此类输入，也应给出受控错误而非进程退出。
5. 成功时验证源 Mesh 不变、JOIN_BRANCH 完整、UV/权重策略一致，并分别验证
   rollback、commit、save/reopen。用户选择、模式和视图不得被意外改动。
6. 不以 `preflight=ready` 或 Python 单元测试通过代替 Blender 4.2 活体执行证据。

本次仅记录故障，未重放原生崩溃请求、未修工具、未提交。
