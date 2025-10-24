# 🔴 NextGen 利用率异常高的根本原因分析

## 问题现象

在小规模实验（1000实例，4节点）中，NextGen 的利用率异常高：

```
算法                              成功率   AvgUtil   CPUUtil   MemUtil
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mesos DRF (NSDI'11 源码)        100.0%    43.9%     43.2%     43.9%
Tetris (SIGCOMM'14 公式)        100.0%    25.0%     24.6%     25.0%
NextGen Scheduler (Prototype)   100.0%    84.4%     82.1%     84.4%
                                          ^^^^^^     ^^^^^^^^^^^^
                                          差距近2倍！
```

**关键疑问**：为什么 NextGen 的利用率（84.4%）是 Mesos DRF（43.9%）的近 2 倍？

---

## 🔍 根本原因：采样方式不一致

### 1. Mesos DRF / Tetris 的采样方式（事件驱动）

**代码位置**: `tools/run_with_events.py` 第 206-231 行

```python
def enable_event_driven_simulation(baseline_scheduler_func, tasks, machines, 
                                   batch_step_seconds=300):
    # 主模拟循环
    while events or running_tasks:
        # 步骤1: 处理任务完成事件（释放资源）
        # 步骤2: 运行调度器
        # 步骤3: ⭐ 采样当前利用率
        if running_tasks or len([m for m in machines if m.cpu_used > 0]) > 0:
            current_utils = [max(m.cpu_used/m.cpu, m.mem_used/m.mem) for m in machines]
            util_samples.append(sum(current_utils) / len(current_utils))
        
        # 步骤4: 推进时间
        current_time += batch_step_seconds  # ⭐ 按时间推进
```

**采样特点**：
- **推进单位**：时间（秒）
- **采样频率**：每 `batch_step_seconds` 秒采样一次（默认 10-60 秒）
- **采样内容**：当前时刻所有节点的 **实时利用率**
- **包含空闲**：✅ YES - 包含任务完成后的空闲时间

**时间轴示例** (batch_step=10秒):
```
t=0:    调度批次1 → 放置100个任务 → 采样: 利用率80%
t=10:   调度批次2 → 部分任务完成 → 采样: 利用率60%
t=20:   调度批次3 → 大部分任务完成 → 采样: 利用率30%
t=30:   调度批次4 → 新任务到达 → 采样: 利用率50%
...
平均利用率 = (80 + 60 + 30 + 50 + ...) / N = 43.9%
```

---

### 2. NextGen Scheduler 的采样方式（静态循环）

**代码位置**: `tools/run_complete_comparison.py` 第 1502-1513 行

```python
def run_nextgen_scheduler(tasks, num_machines):
    # 主调度循环
    while scheduled + failed < total_tasks:
        # 步骤1: 释放已完成任务（基于 current_time）
        if use_dynamic_release:
            released_count = sum(m.release_completed_tasks(current_time) 
                                for m in machines)
        
        # 步骤2: 调度下一个任务
        task_tuple = selector.pop_next(now_ms=current_time)
        # ... 选择节点，放置任务 ...
        scheduled += 1
        
        # 步骤3: ⭐ 每调度100个任务采样一次
        if (scheduled + failed) % sample_interval == 0:
            current_utils = [m.utilization() for m in machines]
            util_samples.append(sum(current_utils) / len(current_utils))
        
        # 步骤4: 推进到下一个任务到达时间
        current_time = max(current_time, next_task.arrival)  # ⭐ 按任务数推进
```

**采样特点**：
- **推进单位**：任务数（个）
- **采样频率**：每调度 100 个任务采样一次
- **采样内容**：当前已调度任务的 **累积占用**
- **包含空闲**：❌ NO - 只在"正在调度任务"时采样

**任务进度示例**:
```
任务0-100:   快速调度 → 采样: 利用率80% (100个任务占用资源)
任务101-200: 快速调度 → 采样: 利用率82% (200个任务占用资源)
任务201-300: 快速调度 → 采样: 利用率85% (300个任务占用资源)
任务301-400: 快速调度 → 采样: 利用率87% (400个任务占用资源)
...
平均利用率 = (80 + 82 + 85 + 87 + ...) / N = 84.4%
```

**关键问题**：
- NextGen 的 `current_time` 虽然有推进，但**采样触发条件是任务数，不是时间**
- 即使任务完成释放了资源，只要还在快速调度新任务，采样点就会一直在"高利用率"时刻
- **空闲时间被完全忽略**

---

## 📊 详细对比

| 维度 | Mesos/Tetris<br>(事件驱动) | NextGen<br>(静态循环) | 差异 |
|------|---------------------------|---------------------|------|
| **推进方式** | 按时间 (秒) | 按任务数 (个) | ⚠️ 不一致 |
| **采样触发** | 每 N 秒 | 每 M 个任务 | ⚠️ 不一致 |
| **采样内容** | 当前实时利用率 | 累积占用率 | ⚠️ 不一致 |
| **资源释放** | ✅ 自动按时间释放 | ⚠️ 释放但不影响采样 | ⚠️ 不一致 |
| **空闲时间** | ✅ 包含 | ❌ 不包含 | ❌ 不公平 |
| **利用率含义** | 时间加权平均 | 任务数加权平均 | ❌ 不可比 |

---

## 🧪 数值模拟验证

假设场景：
- 1000 个任务，每个运行 10 秒
- 4 个节点，每个容量 11 core
- 每个任务需求 0.1 core

### Mesos/Tetris（事件驱动，batch_step=10秒）

```
时间轴：
t=0:     调度250个任务 → 节点利用率 = 250*0.1/44 = 56.8%
t=10:    前250个完成，调度新250个 → 利用率 = 250*0.1/44 = 56.8%
t=20:    前250个完成，调度新250个 → 利用率 = 250*0.1/44 = 56.8%
t=30:    前250个完成，调度新250个 → 利用率 = 250*0.1/44 = 56.8%
t=40:    所有完成 → 利用率 = 0%

采样: [56.8%, 56.8%, 56.8%, 56.8%, 0%]
平均利用率 = (56.8*4 + 0) / 5 = 45.4%
```

### NextGen（静态循环，每100任务采样）

```
任务进度：
任务0-100:    调度中 → 节点利用率 = 100*0.1/44 = 22.7%
任务101-200:  调度中 → 节点利用率 = 200*0.1/44 = 45.5%
任务201-300:  调度中 → 节点利用率 = 300*0.1/44 = 68.2%
任务301-400:  调度中 → 节点利用率 = 400*0.1/44 = 90.9%
任务401-500:  调度中，前100个完成 → 利用率 = 400*0.1/44 = 90.9%
...
任务901-1000: 调度中，前500个完成 → 利用率 = 500*0.1/44 = 113.6% (超容量，实际受限)

采样: [22.7%, 45.5%, 68.2%, 90.9%, 90.9%, ..., 90%+]
平均利用率 = 85%+
```

**结论**：NextGen 的利用率被 **高估了近 2 倍**！

---

## ✅ 修复方案

### 方案 A：让 NextGen 也使用事件驱动模拟（推荐）✅

**修改**: 将 `run_nextgen_scheduler()` 改为调用 `enable_event_driven_simulation()`

**代码示例**:
```python
def run_nextgen_scheduler(tasks: List[Task], num_machines: int = 114) -> dict:
    print("\n━━━ [5/5] NextGen Scheduler (Layered) ━━━")
    
    machines = [Machine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]
    
    # 定义批量调度函数
    def nextgen_schedule_batch(batch_tasks, current_machines):
        """
        NextGen 批量调度逻辑
        返回 placements: [(task_id, machine_id), ...]
        """
        placements = []
        # ... 使用 NextGen 的打分逻辑 ...
        return placements
    
    # ⭐ 启用事件驱动模拟（与 Mesos/Tetris 保持一致）
    durations = [t.duration for t in tasks if t.duration > 0]
    median_duration = int(np.median(durations)) if durations else 60
    recommended_step = max(1, min(median_duration // 2, 60))
    batch_step = int(os.getenv("BATCH_STEP_SECONDS", str(recommended_step)))
    
    print(f"  [事件驱动] 调度间隔={batch_step}秒 (任务中位时长={median_duration}秒)")
    
    result = enable_event_driven_simulation(
        baseline_scheduler_func=nextgen_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=batch_step,
    )
    
    result["name"] = "NextGen Scheduler (Prototype)"
    return result
```

**优点**：
- ✅ 与基线算法采样方式完全一致
- ✅ 公平对比
- ✅ 保留动态资源释放的优势

**预期结果**：
- NextGen 利用率：84.4% → **50-60%**（更真实）
- 与 Mesos/Tetris 可比

---

### 方案 B：禁用 NextGen 的动态资源释放（对比基准）

**命令**:
```bash
export NEXTGEN_DYNAMIC_RELEASE=0
python tools/run_complete_comparison.py ./data 1000 4
```

**效果**：
- NextGen 变成"静态快照"模式
- 失去动态资源释放的优势
- 利用率会进一步降低

**用途**：
- 作为消融实验，验证动态资源释放的收益
- 不适合作为主要对比结果

---

### 方案 C：修改基线算法的采样逻辑（不推荐）

修改 `run_with_events.py`，只在"有任务运行"时采样：

```python
# ⚠️ 不推荐：违背事件驱动的本意
if running_tasks:  # 只在有任务运行时采样
    util_samples.append(avg_util_now)
```

**问题**：
- ❌ 违背了真实系统的行为（真实集群的利用率包含空闲时间）
- ❌ 高估了基线算法的性能
- ❌ 不符合学术界的评估标准

---

## 🚀 快速修复命令

### 临时禁用 NextGen 的动态资源释放（快速验证）

```bash
# 方案1: 禁用动态资源释放
export NEXTGEN_DYNAMIC_RELEASE=0
python tools/run_complete_comparison.py ./data 1000 4

# 方案2: 只对比静态模式的基线算法
export ENABLE_FIRMAMENT=0
export ENABLE_SLO_DRIVEN=1  # SLO-Driven 是静态模式，可作为参考
python tools/run_complete_comparison.py ./data 1000 4
```

### 正确修复（需要代码改动）

```bash
# 1. 检查当前分支
git status

# 2. 创建修复分支
git checkout -b fix/nextgen-event-driven-sampling

# 3. 修改代码（见上面的方案A）
# 编辑 tools/run_complete_comparison.py 的 run_nextgen_scheduler() 函数

# 4. 测试
export BATCH_STEP_SECONDS=10
python tools/run_complete_comparison.py ./data 1000 4

# 5. 验证结果（利用率应该接近 Mesos/Tetris）
```

---

## 📖 相关文档

- **事件驱动实现**: `docs/completed-event-driven-implementation.md`
- **基线资源管理**: `docs/baselines-resource-management-analysis.md`
- **采样差异诊断**: 运行 `/tmp/check_sampling_diff.py`

---

## 🔧 验证清单

修复后，验证以下指标：

### 1. 利用率接近

```bash
# 预期结果（修复后）:
算法                    成功率   AvgUtil
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mesos DRF              100.0%    43.9%
Tetris                 100.0%    25.0%
NextGen (修复后)        100.0%    ~48%   # 应该接近 Mesos
```

### 2. 调度轮次相同

```bash
# 查看输出中的 [事件驱动统计]
[事件驱动统计]
  调度轮次: ~100       # 三者应该相同
  已调度: 1000
  已释放任务: ~900
```

### 3. 采样次数相同

```bash
[事件驱动统计]
  采样次数: ~100       # 三者应该相同
  过程平均利用率: ~45%
```

---

## 📝 结论

### 问题根源
NextGen 使用**按任务数推进**的静态循环模式，而 Mesos/Tetris 使用**按时间推进**的事件驱动模式，导致采样方式根本不一致。

### 不公平性
- NextGen 的采样只在"正在调度任务"的高峰时刻
- Mesos/Tetris 的采样包含整个时间轴（包括空闲时间）
- 结果：NextGen 利用率被**高估近 2 倍**

### 修复方向
✅ **推荐**: 让 NextGen 也使用事件驱动模拟（方案A）
- 保持与基线算法一致的评估标准
- 公平对比
- 符合学术界规范

---

**最后更新**: 2025-10-22  
**问题状态**: 🔴 已识别，待修复  
**影响范围**: 所有使用 NextGen 的对比实验结果
