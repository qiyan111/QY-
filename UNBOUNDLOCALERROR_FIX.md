# UnboundLocalError 修复

## 问题

运行 `run_complete_comparison.py` 时报错：

```
UnboundLocalError: local variable 'real_used' referenced before assignment
File "tools/run_complete_comparison.py", line 1529
```

## 根本原因

在 `analyze_result()` 函数中，`real_used` 变量只在 `else` 分支（静态模式）中定义，但在后续代码（第 1529 行）中无条件使用，导致在事件驱动模式下未定义。

### 错误的代码流程

```python
# 第 1441-1455 行
if 'effective_util_over_time' in result:
    # 事件驱动模式
    effective_util = result['effective_util_over_time']
    # ❌ real_used 未定义
else:
    # 静态模式
    real_used = 0.0  # ✅ 这里定义了
    for m in machines:
        real_used += ...

# 第 1529 行
print(f"Σreal_cpu={real_used:.1f}")  # ❌ 如果走 if 分支会报错
```

## 修复方案

在事件驱动模式分支中也定义 `real_used` 变量：

```python
if 'effective_util_over_time' in result:
    # 事件驱动模式
    effective_util = result['effective_util_over_time']
    waste_rate = 1.0 - effective_util
    # ✅ 添加这两行
    capacity_total = len(machines) * 11.0
    real_used = effective_util * capacity_total
else:
    # 静态模式
    real_used = 0.0
    for m in machines:
        used_real = sum(getattr(task_dict[tid], "real_cpu", 0.0) for tid, _ in m.tasks)
        real_used += used_real
    capacity_total = len(machines) * 11.0
    effective_util = real_used / capacity_total if capacity_total else 0.0
    waste_rate = 1.0 - effective_util
```

## 修复位置

- **文件**: `tools/run_complete_comparison.py`
- **函数**: `analyze_result()`
- **行号**: 第 1441-1455 行

## 验证

```bash
# 语法检查
python3 -c "
with open('tools/run_complete_comparison.py', 'r') as f:
    compile(f.read(), 'run_complete_comparison.py', 'exec')
print('✓ 语法检查通过')
"

# 重新运行实验
export BATCH_STEP_SECONDS=10
python tools/run_complete_comparison.py ./data 1000 4
```

## 状态

✅ 已修复，可以正常运行

---

**修复时间**: 2025-10-22  
**影响**: 所有使用事件驱动模式的调度器（Mesos, Tetris, NextGen）
