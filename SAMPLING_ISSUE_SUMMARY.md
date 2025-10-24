# 🎯 采样不一致问题 - 快速摘要

## ❓ 您的问题

> NextGen 的利用率（84.4%）远高于 Mesos DRF（43.9%）和 Tetris（25.0%），为什么差这么多？

## ✅ 答案

**根本原因**：采样方式不一致，导致不公平对比。

| 算法 | 采样方式 | 推进单位 | 包含空闲时间 |
|------|----------|----------|--------------|
| Mesos DRF | 事件驱动 | 时间（秒） | ✅ 是 |
| Tetris | 事件驱动 | 时间（秒） | ✅ 是 |
| NextGen | 静态循环 | 任务数（个） | ❌ **否** |

**结果**：
- Mesos/Tetris 的利用率包含了"任务完成后的空闲时间"
- NextGen 的利用率只统计"正在调度任务的高峰时刻"
- **NextGen 的利用率被高估了近 2 倍**

---

## 🔍 详细分析

### Mesos/Tetris（事件驱动）

```python
# 代码: tools/run_with_events.py
while events or running_tasks:
    # 1. 处理任务完成事件（释放资源）
    # 2. 运行调度器
    # 3. 采样利用率
    current_time += batch_step_seconds  # ⭐ 按时间推进
```

**时间轴**：
```
t=0:  调度 → 利用率 80%
t=10: 部分完成 → 利用率 60%
t=20: 大部分完成 → 利用率 30%
t=30: 新任务到达 → 利用率 50%
平均 = (80+60+30+50)/4 = 55%  ✅ 包含空闲时间
```

### NextGen（静态循环）

```python
# 代码: tools/run_complete_comparison.py
while scheduled + failed < total_tasks:
    # 1. 调度下一个任务
    # 2. 每100个任务采样一次
    scheduled += 1  # ⭐ 按任务数推进
```

**任务进度**：
```
任务0-100:   快速调度 → 利用率 80%
任务101-200: 快速调度 → 利用率 85%
任务201-300: 快速调度 → 利用率 90%
...
平均 = (80+85+90+...)/N = 85%  ❌ 只在高峰时刻采样
```

---

## 🚀 快速验证

### 方法1：查看调试输出

```bash
export BATCH_STEP_SECONDS=10
python tools/run_complete_comparison.py ./data 1000 4 2>&1 | grep "事件驱动统计"
```

**预期输出**：
```
# Mesos DRF
[事件驱动统计]
  调度轮次: 95
  采样次数: 95

# Tetris  
[事件驱动统计]
  调度轮次: 95
  采样次数: 95

# NextGen
[动态资源管理]
  采样次数: 10    # ❌ 与上面不一致！
```

**结论**：NextGen 的采样次数远少于 Mesos/Tetris，说明采样触发条件不同。

---

### 方法2：禁用 NextGen 的动态资源释放

```bash
export NEXTGEN_DYNAMIC_RELEASE=0
python tools/run_complete_comparison.py ./data 1000 4
```

**预期结果**：
- NextGen 利用率会**降低**（因为失去了动态释放的优势）
- 但仍然比 Mesos/Tetris 高（因为采样方式仍不一致）

---

### 方法3：运行验证脚本

```bash
bash tools/verify_sampling_consistency.sh
```

---

## ✅ 修复方案

### 推荐：让 NextGen 也使用事件驱动模拟

**修改**: `tools/run_complete_comparison.py` 的 `run_nextgen_scheduler()` 函数

**代码结构**（伪代码）：
```python
def run_nextgen_scheduler(tasks, num_machines):
    # 定义批量调度函数
    def nextgen_schedule_batch(batch_tasks, current_machines):
        # NextGen 的打分逻辑
        placements = []
        for task in batch_tasks:
            # 使用 TenantSelector, WatermarkGuard, score_node 等
            best_machine = find_best_machine(task, current_machines)
            if best_machine:
                placements.append((task.id, best_machine.id))
        return placements
    
    # ⭐ 调用事件驱动模拟（与 Mesos/Tetris 保持一致）
    result = enable_event_driven_simulation(
        baseline_scheduler_func=nextgen_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=10,
    )
    return result
```

**优点**：
- ✅ 与基线算法采样方式完全一致
- ✅ 公平对比
- ✅ 保留动态资源释放

**预期效果**：
- NextGen 利用率：84.4% → **50-60%**（更真实）
- 与 Mesos/Tetris 可比

---

## 📊 修复后的预期结果

```
算法                              成功率   AvgUtil   差异
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mesos DRF (NSDI'11 源码)        100.0%    43.9%     (基准)
Tetris (SIGCOMM'14 公式)        100.0%    25.0%     -18.9%
NextGen (修复后)                100.0%    ~48%      +4.1%  ✅ 合理
```

**解释**：
- NextGen 比 Mesos 高 5-10% 是**合理的**（因为有更好的打分算法）
- NextGen 比 Mesos 高 2 倍是**不合理的**（采样方式不一致）

---

## 📖 详细文档

- 完整分析：`/workspace/NEXTGEN_SAMPLING_ISSUE.md`
- 验证脚本：`/workspace/tools/verify_sampling_consistency.sh`
- 对比分析：运行 `python3 /tmp/check_sampling_diff.py`

---

## 🎯 下一步行动

### 短期（立即）
```bash
# 1. 验证问题存在
bash tools/verify_sampling_consistency.sh

# 2. 查看详细分析
cat NEXTGEN_SAMPLING_ISSUE.md
```

### 中期（需要代码修改）
1. 将 `run_nextgen_scheduler()` 改为调用 `enable_event_driven_simulation()`
2. 重新运行实验
3. 验证利用率接近 Mesos/Tetris

### 长期（论文/报告）
- 在论文中明确说明采样方式
- 提供修复前后的对比结果
- 解释为什么事件驱动采样更公平

---

**关键结论**：
> NextGen 的高利用率（84.4%）不是因为算法更好，  
> 而是因为**采样方式不一致**导致的统计偏差。  
> 修复后，NextGen 的真实利用率应该在 50-60% 左右。

---

**最后更新**: 2025-10-22  
**问题状态**: 🔴 已识别，需修复  
**优先级**: P0（影响所有对比实验结果）
