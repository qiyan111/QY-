# 基线算法利用率异常问题分析与修复

## 🔴 问题现象

运行 `run_complete_comparison.py` 后得到的结果异常：

```
算法                                   成功率   AvgUtil   CPUUtil   MemUtil       碎片率    实用Util    最大Util       失配率
----------------------------------------------------------------------------------------------------
Mesos DRF (NSDI'11 源码)             92.7%      5.4%      4.2%      5.4%     94.6%      1.4%     99.9%      0.0%
Tetris (SIGCOMM'14 公式)             18.7%      1.1%      1.0%      1.1%     98.9%      0.2%    100.0%      0.0%
NextGen Scheduler (Prototype)     100.0%     52.6%     42.4%     52.6%     47.4%     21.1%     94.6%      0.0%
```

**异常点**：
- ❌ Mesos DRF 利用率只有 5.4%（预期应该 >75%）
- ❌ Tetris 成功率只有 18.7%，利用率只有 1.1%（预期应该 >70%）
- ✅ NextGen 表现正常（100% 成功率，52.6% 利用率）

---

## 🔍 根本原因分析

### 原因 1：调度间隔过大

**代码位置**: `run_complete_comparison.py` 第 522-527 行

```python
durations = [t.duration for t in tasks if t.duration > 0]
median_duration = int(np.median(durations)) if durations else 60
recommended_step = max(1, min(median_duration // 2, 60))
batch_step = int(os.getenv("BATCH_STEP_SECONDS", str(recommended_step)))
```

**问题**：
- Alibaba 2018 Trace 中任务时长中位数可能较大（例如 300 秒）
- 推荐的调度间隔 = min(150, 60) = **60 秒**
- 每 60 秒才调度一次，导致：
  1. 第一批调度时资源不足，很多任务失败
  2. 任务完成释放资源后，要等 60 秒才会重新调度
  3. 大部分时间集群处于"空闲等待"状态

**影响**：
```
时间轴示例（调度间隔 60 秒）：
t=0:    调度 1000 任务 → 放置 200 个（资源不足）
t=60:   资源已释放，但只调度新到达任务（800 个失败任务被遗忘）
t=120:  继续调度新任务...

结果：利用率低（5.4%），成功率低（92.7%）
```

---

### 原因 2：缺少任务重试机制

**代码位置**: `tools/run_with_events.py` 第 199-203 行（修复前）

```python
# 记录调度失败的任务
for task in pending_tasks:
    if task.id not in scheduled_ids:
        failed_count += 1  # ❌ 直接标记失败，不重试
```

**问题**：
- 真实 Firmament: 失败任务会重新加入 `runnable_tasks_` 队列
- 真实 Mesos: 支持 `framework->resubmitTask()` 重试
- 我们的实现：失败即失败 ❌

**影响**：
- 第一批调度时因资源不足失败的任务，永远不会重试
- 即使后续资源释放，这些任务也无法被调度

---

### 原因 3：事件驱动模拟的"空闲时间稀释效应"

**代码位置**: `tools/run_with_events.py` 第 206-231 行

```python
# ⭐ 只在有运行中的任务时才采样（避免空闲时间稀释利用率）
if running_tasks or len([m for m in machines if m.cpu_used > 0 or m.mem_used > 0]) > 0:
    # 采样利用率...
```

**问题**：
- 即使修正了采样逻辑（只在有任务运行时采样），调度间隔过大仍会导致：
  ```
  t=0-59:   有任务运行，采样利用率
  t=60-119: 大部分任务已完成，利用率下降
  t=120:    新一批任务到达...
  
  平均利用率 = (高利用率时段 + 低利用率时段) / 2 = 偏低
  ```

---

## ✅ 修复方案

### 修复 1：减小调度间隔

**推荐设置**：
```bash
# 方案 A：固定 10 秒间隔（适合大多数场景）
export BATCH_STEP_SECONDS=10
python tools/run_complete_comparison.py ./data 100000 80

# 方案 B：更激进的 5 秒间隔（适合任务时长较短的场景）
export BATCH_STEP_SECONDS=5
python tools/run_complete_comparison.py ./data 100000 80
```

**预期效果**：
- 调度频率提高 6-12 倍
- 任务完成后能快速重新调度
- 利用率提升至 **75%+**

---

### 修复 2：启用任务重试机制 ✅ 已实现

**代码修改**: `tools/run_with_events.py`

```python
# 新增配置
enable_retry = os.getenv("ENABLE_TASK_RETRY", "1") == "1"  # 默认启用
max_retries = int(os.getenv("MAX_TASK_RETRIES", "3"))      # 最多重试 3 次

# 失败任务处理
for task in pending_tasks:
    if task.id not in scheduled_ids:
        if enable_retry:
            retry_count = retry_counts.get(task.id, 0)
            if retry_count < max_retries:
                # ⭐ 重新加入队列，下一批次重试
                retry_counts[task.id] = retry_count + 1
                heapq.heappush(events, (current_time + batch_step_seconds, 
                                       event_counter, 'TASK_SUBMIT', task))
                event_counter += 1
            else:
                failed_count += 1
        else:
            failed_count += 1
```

**使用方法**：
```bash
# 启用重试（默认已启用）
export ENABLE_TASK_RETRY=1
export MAX_TASK_RETRIES=3

# 禁用重试（对比测试）
export ENABLE_TASK_RETRY=0
```

**预期效果**：
- Tetris 成功率：18.7% → **95%+**
- Mesos DRF 成功率：92.7% → **99%+**

---

### 修复 3：动态调整节点数

**代码位置**: `run_complete_comparison.py` 第 1725-1736 行

```python
# 根据任务需求动态调整节点数
total_mem = sum(t.mem for t in tasks)
target_util = float(os.getenv("TARGET_UTIL", "1.0"))
num_machines = math.ceil(total_mem / 11.0 / target_util) + safety_buffer
```

**推荐设置**：
```bash
# 允许更高利用率（0.9 = 90%）
export TARGET_UTIL=0.9

# 增加安全缓冲节点
export TARGET_BUFFER_NODES=5
```

---

## 📊 修复后预期结果

| 调度器 | 成功率 (修复前 → 后) | 利用率 (修复前 → 后) |
|--------|---------------------|---------------------|
| Mesos DRF | 92.7% → **99.5%+** | 5.4% → **78%+** |
| Tetris | 18.7% → **96%+** | 1.1% → **72%+** |
| NextGen | 100% → **100%** | 52.6% → **55%+** |

---

## 🚀 快速修复命令

### 完整修复（推荐）
```bash
# 设置环境变量
export BATCH_STEP_SECONDS=10      # 减小调度间隔
export ENABLE_TASK_RETRY=1        # 启用重试（默认已启用）
export MAX_TASK_RETRIES=3         # 最多重试 3 次
export TARGET_UTIL=0.9            # 允许 90% 利用率

# 运行对比实验
python tools/run_complete_comparison.py ./data 100000 80
```

### 调试模式（小规模测试）
```bash
export BATCH_STEP_SECONDS=5
export ENABLE_TASK_RETRY=1
export DEBUG_EVENT_LOOP=1

# 只调度 1000 个任务，10 个节点
python tools/run_complete_comparison.py ./data 1000 10
```

### 性能对比测试
```bash
# 测试不同调度间隔的影响
for step in 5 10 30 60; do
    echo "========== BATCH_STEP_SECONDS=$step =========="
    export BATCH_STEP_SECONDS=$step
    python tools/run_complete_comparison.py ./data 10000 50 | grep "平均利用率"
done
```

---

## 📖 相关文档

- **问题分析**: `docs/baselines-resource-management-analysis.md`
- **事件驱动实现**: `docs/completed-event-driven-implementation.md`
- **修复总结**: `BUG_FIXES_SUMMARY.md`

---

## 🔧 故障排查

### 如果修复后仍有问题

#### 1. 检查 trace 数据
```bash
ls -lh ./data/
# 应包含: batch_task.csv, batch_instance.csv, usage_avg.csv
```

#### 2. 启用详细日志
```bash
export DEBUG_EVENT_LOOP=1
python tools/run_complete_comparison.py ./data 1000 10
```

#### 3. 查看调度轮次统计
```bash
# 输出应包含：
#   调度轮次: ~1000+
#   已调度: ~950+
#   重试统计: ~200 个任务重试, 总重试次数: ~500
```

#### 4. 验证资源释放
```bash
# 输出应包含：
#   已释放任务: ~800+
#   仍在运行: ~150
```

---

**最后更新**: 2025-10-22  
**修复版本**: v2.1  
**修复状态**: ✅ 已实现任务重试机制，需测试验证
