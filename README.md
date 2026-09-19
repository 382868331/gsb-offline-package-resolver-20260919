# 离线版本依赖与特性求解库

在完全离线的包注册表上解析版本与命名特性，输出可独立复核的依赖锁单。纯 Python 3.14 标准库，Windows 原生可运行，无网络、无第三方依赖。

## 运行

演示（一个正常 sat、一个真实 unsat、一个预算 unknown，结果均由代码实际计算）：

```
python demo.py
```

测试：

```
python -m unittest discover -s tests -v
```

## 接口

```python
from resolver import solve, check_lock, brute_force_locks, RegistryError
```

### `solve(registry, requests, budget=10000) -> dict`

- `registry`：包对象列表（至多 12 包、每包至多 6 版本，超限拒绝）：
  ```python
  {"name": "web", "versions": [
      {"version": [1, 0, 0],                       # 非负整数三元组
       "deps": [{"package": "json",                 # 必需依赖
                 "ranges": [[[1, 0, 0], [2, 0, 0]]], # 下闭上开区间之并；null 表示无界
                 "features": ["ssl"]}],              # 对目标包的特性请求
       "features": {"tls": [ /* 同 deps 结构的额外依赖 */ ]}},
  ]}
  ```
- `requests`：根请求列表，每个含唯一 `id`、`package`，可选 `ranges` / `features`。
- 返回：
  - `{"status": "sat", "roots", "lock", "nodes_used"}` — `lock` 含每包的版本、最终特性集合、带来源的依赖列表；
  - `{"status": "unsat", "roots", "conflicts", "nodes_used"}` — 附根 ID 与冲突诊断（非极小核）；
  - `{"status": "unknown", "roots", "reason", "nodes_used"}` — 节点预算耗尽（每尝试一个包版本计一节点，整个请求共用）。

### `check_lock(registry, requests, lock) -> list[str]`

独立锁单检查器。从注册表与根请求重新推导约束累积、特性不动点与可达闭包，逐项核对锁单：根 ID、版本满足全部约束、声明特性等于传播不动点、声明依赖等于激活依赖集、锁单包集合恰好是根的最小可达闭包（无缺失、无无关包）。返回问题列表，空列表表示通过。

### `brute_force_locks(registry, requests) -> set`

穷举真值枚举器（仅适用于 ≤4 包的小表），返回所有合法锁单（`frozenset[(pkg, version)]` 的集合），用于交叉验证求解器的 sat/unsat。

## 语义要点

- 同包多来源约束取交；每包只选一个版本；同包特性请求取并；无默认特性。
- 特性可增加依赖及目标特性请求；启用集合传播到不动点，允许循环；被请求特性在选定版本上不存在时该候选不可行。
- 搜索确定性：传播后按最小包名选未决包，版本降序尝试，冲突回溯并撤销该分支的特性/依赖影响，返回该顺序下的首个解（非全局最优）。
- 校验拒绝：重复包名、重复版本、重复根 ID、不存在的包引用、非法版本三元组。

## 模块结构

- `resolver/model.py` — 数据模型、校验、区间代数
- `resolver/solver.py` — 回溯求解器与不动点传播
- `resolver/checker.py` — 独立锁单检查器
- `resolver/brute.py` — 穷举交叉验证
- `tests/test_resolver.py` — 语义、检查器、校验与随机交叉验证测试
