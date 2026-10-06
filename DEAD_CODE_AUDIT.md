# 死代码审核与清理清单

> 审核范围：`hestia/`（123 个 `.py`）+ `vendor/`（79 个 `.py`）
> 检测手段：ruff（pyflakes F 系列 + bugbear B 系列 + pylint 部分规则）、vulture、定向搜索、人工核查
> 状态：已完成，回归测试通过（31 passed + 20 passed）

---

## 一、已清理的死代码（84 处显式删除 + 5 处连带解决）

### 1. 未使用导入 F401（74 处）
- `ruff --fix` 自动删除 **70 处**（普通文件里的真死导入）
- 手动删除 **4 处** `__init__.py` 真死导入：
  - `hestia/orchestration/__init__.py`：`typing.Optional`
  - `hestia/skills/__init__.py`：`os`、`json`、`datetime.datetime`

### 2. 未使用局部变量 F841（7 处）

| 文件 | 变量 | 处理方式 |
|---|---|---|
| `hestia/channels/openclaw_bridge.py` | `upstream` | 删除（连同其说明注释） |
| `hestia/learning/spaced_repetition.py` | `now` | 删除 |
| `vendor/hkc/hkc_core/graph/sqlite_store.py` | `rel_type` | 改为 `_` 占位解包 |
| `vendor/hmr/engines/evolution.py` | `clusters` | 删除 |
| `vendor/hmr/engines/jit_compiler.py` | `seen_ids` | 删除 |
| `vendor/hmr/engines/lifecycle.py` | `pruned` | 保留调用、去掉赋值 |
| `vendor/hmr/engines/scheduler.py` | `total` | 删除 |

### 3. 未使用循环变量 B007（3 处）

| 文件 | 变量 | 处理方式 |
|---|---|---|
| `hestia/itinerary/reasoning.py` | `i` | `enumerate` → 直接迭代 |
| `hestia/orchestration/__init__.py` | `aid` | `items()` → `values()` |
| `vendor/hmr/engines/evolution.py` | `mtype` | `items()` → `values()` |

### 4. 重复定义 F811（4 处，随导入删除连带解决）
顶层未使用导入被同名局部导入覆盖：`base.py` 的 `asyncio`、`api.py` 的 `threading`、`http_client.py` 的 `HKCError` 等。

### 5. 导入遮蔽 F402（1 处，随导入删除连带解决）
`hestia/core/thought_chain.py` 的 `field` 导入被循环变量遮蔽。

---

## 二、深入核查后判定为「非死代码，保留」

| 项 | 数量 | 保留原因 |
|---|---|---|
| `__init__.py` 公开重导出 | 10 | `hestia.kernel`/`hestia.cognitive` 被测试与各模块大量 `from ... import`，属公开 API |
| 接口参数 | 5 | `reasoner.py` 基类参数（子类覆盖契约）、`middleware.py` 的 `__exit__(*exc)`（协议签名）、`cwg.py` 的 `add_edge(relationship=...)`（docstring 声明的预留接口） |
| `except Exception: pass` | 4 | `container.py` 的 `close()` 资源清理防御，逐个关闭、单点失败不影响其余 |
| `raise NotImplementedError` | 2 | `base.py` 抽象方法保护、`action_executor.py` 默认分支保护 |

---

## 三、各维度核查结论

| 维度 | 结论 |
|---|---|
| 未使用导入 / 变量 / 循环变量 | 已清理干净（ruff 残留 0，除 10 个公开重导出） |
| 未使用函数 / 类 / 方法 | `vulture` 70% 置信度无发现 |
| 无效分支（`if True/False`、`while False`、`assert False`） | 0 处 |
| 空实现 / 占位函数 | 0 处 |
| 重复代码块（复制粘贴死代码） | 未发现明显项（ruff `SIM101` 0 处；定向探索无高置信度发现） |

---

## 四、验证

- `python -m compileall hestia vendor` → 编译通过（exit 0）
- `test_agent_lifecycle` + `test_kernel` + `test_capabilities` + `test_event_spine` + `test_policy_gate` + `test_memory_recall` + `test_integration` → **31 passed**
- `test_scheduler` + `test_pipeline` → **20 passed**
