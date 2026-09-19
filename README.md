# 离线版本依赖与特性求解库

在完全离线的小型包注册表上，根据根请求解析**版本选择**与**特性传播**，
输出可被独立检查器复核的锁单。仅使用 Python 3.14 标准库，无网络、无第三方
依赖、无安装器、无真实仓库，Windows 原生离线可运行。

## 规则摘要

- 规模上限：12 个包，每包至多 6 个版本；版本是非负整数三元组
  `(major, minor, patch)`，按字典序比较，**不实现完整 SemVer**。
- 依赖约束是若干**下闭上开**区间 `[low, high)` 的并集；同一包来自多个
  来源的约束取**交集**；每包在解中恰好选定一个版本。
- 每个版本有必需依赖和命名特性；特性可再增加依赖、向（其他包或本包的）
  特性发请求。特性启用集合传播到**最小固定点**，允许循环；同包多来源的
  特性取并集；没有默认特性。候选版本缺少任一被请求特性即不可行。
- 搜索顺序确定：始终选择**名字最小的未决包**，其版本**从高到低**尝试；
- 冲突即回溯，并彻底撤销该分支加入的约束、特性、未决包和选择；
  返回该确定顺序下的**首个**解，不保证全局最优版本向量。
- 节点预算为整个请求共用：每尝试一个包版本计 1 个节点。预算耗尽返回
  `unknown`；只有完整排除全部分支才返回 `unsat`，并附根 ID 与冲突诊断
  （不要求极小冲突核）。
- 锁单恰好是根请求与激活依赖的最小可达闭包，注册表中的无关包不会入锁。

## 接口

```python
from resolver import build_registry, build_request, solve, check_lock

registry = build_registry({
    "packages": [
        {
            "name": "webapp",
            "versions": [
                {
                    "version": [2, 0, 0],
                    "dependencies": {"server": [[[2, 0, 0], [3, 0, 0]]]},
                    "features": {
                        "http2": {
                            "dependencies": {"crypto": [[[1, 0, 0], [2, 0, 0]]]},
                            "feature_requests": {"server": ["tls"]},
                        }
                    },
                }
            ],
        },
        # ... 至多 12 个包
    ]
})

request = build_request(
    {
        "id": "build-42",                       # 根请求唯一 ID
        "requirements": {"webapp": None},       # None 表示任意版本
        "feature_requests": {"webapp": ["http2"]},
    },
    registry,
)

result = solve(registry, request, node_budget=10_000)
result.status        # "sat" | "unsat" | "unknown"
result.root_id
result.nodes_used
result.versions             # sat: {包名: [major, minor, patch]}
result.features             # sat: {包名: [最终启用特性...]}（固定点）
result.dependency_sources   # sat: {包名: [要求来源...]}，根包标根 ID
result.conflicts            # unsat: 冲突诊断列表（类型/包/来源等）

lock = result.to_dict()                    # JSON 可序列化的锁单
check = check_lock(registry, request, lock)
check.ok                                   # 独立复核：根/版本/特性固定点/闭包
check.raise_if_invalid()
```

约束写法：

- `None` / 省略：任意版本；
- `[]`：空并集，任何版本都不满足（用于产生显式冲突）；
- `[[[low], [high]], ...]`：多个下闭上开区间的并，相邻重叠会合并。

批量求解可用 `solve_batch(registry, requests, node_budget)`，批次内根 ID
重复会抛 `DuplicateRootIdError`。

输入校验错误均为 `ResolverError`（`ValueError` 子类）：
`DuplicatePackageError`、`DuplicateVersionError`、`DuplicateRootIdError`、
`UnknownPackageError`、`InvalidInputError`。加载时即拒绝重复包/重复版本/
不存在的包引用（含特性依赖与特性请求目标）。候选版本缺少被请求特性属于
搜索期不可行（`unsat`，诊断类型 `missing_feature`），而非加载错误。

## 独立检查器

`resolver.checker.check_lock` 不信任求解器内部状态，而是根据注册表与原始
根请求重新推导：锁定版本存在且满足根约束与各来源约束；特性集合重新计算
最小固定点（允许循环）并与锁单逐一比对；锁定包集合必须恰好等于可达闭包，
多一个无关包或缺一个可达包均判无效。

`tests/test_crosscheck.py` 另用一个独立的朴素穷举 oracle（≤4 包、每包 ≤3
版本的固定种子随机小表）逐例比对 sat/unsat，并对每个 sat 结果跑检查器。

## 运行方法

演示（约 8 秒内，展示一次真实 sat（最高版本死路回退 + 特性环）和一次
真实触发的 unsat（菱形区间冲突），结果均由库实时计算）：

```bash
python demo.py
```

测试：

```bash
python -m unittest discover -s tests -v
```

## 模块组织

- `resolver/intervals.py`：版本三元组、下闭上开区间并集与交集；
- `resolver/model.py`：注册表/请求数据模型与加载期校验；
- `resolver/engine.py`：确定顺序回溯搜索、特性固定点传播、节点预算；
- `resolver/checker.py`：独立锁单检查器；
- `resolver/errors.py`：输入校验异常；
- `demo.py`：真实 sat/unsat 演示；
- `tests/`：区间、校验、七个规定求解场景、检查器拒绝用例与穷举交叉验证。
