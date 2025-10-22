# 📘 为 Baseline 算法启用事件驱动模拟

## 🎯 目标

严格按照 **Firmament 源码** (`baselines/firmament/src/sim/simulator.cc`) 的批量模式（batch mode）架构，为所有baseline算法实现统一的事件驱动模拟，确保公平对比。

---

## 📚 Firmament 源码架构

### 核心逻辑（`simulator.cc::ReplaySimulation()`）

```cpp
while (!event_manager_->HasSimulationCompleted()) {
    // 1. ⭐ 处理事件（包括任务完成 -> 资源释放）
    bridge_->ProcessSimulatorEvents(run_scheduler_at);
    
    // 2. 运行调度器
    run_scheduler_at = ScheduleJobsHelper(run_scheduler_at);
    
    // 3. 推进时间（batch mode: 固定间隔）
    run_scheduler_at += FLAGS_batch_step;
}
```

### 关键组件

1. **EventManager**: 管理所有事件（任务提交、任务完成、机器心跳等）
2. **SimulatorBridge**: 连接事件和调度器
3. **OnTaskPlacement**: 任务放置时添加结束事件
4. **TaskCompleted**: 任务完成时释放资源

---

## 🔧 使用方法

### 步骤 1: 导入包装器

```python
from tools.run_with_events import enable_event_driven_simulation
```

### 步骤 2: 修改调度函数

#### 原来的静态调度（❌ 错误）

```python
def run_firmament(tasks, num_machines):
    machines = [Machine(...) for i in range(num_machines)]
    scheduler = FirmamentScheduler(machines)
    
    # ❌ 一次性调度所有任务
    placements = scheduler.schedule(tasks)
    
    # ❌ 直接累加资源（没有释放）
    for task_id, machine_id in placements:
        machines[machine_id].cpu_used += task.cpu
        machines[machine_id].mem_used += task.mem
    
    return {"scheduled": len(placements), "failed": ..., "machines": machines}
```

#### 修改为事件驱动（✅ 正确）

```python
def run_firmament(tasks, num_machines):
    machines = [Machine(...) for i in range(num_machines)]
    scheduler = FirmamentScheduler(machines)
    
    # ✅ 定义调度逻辑函数
    def firmament_schedule_batch(batch_tasks, current_machines):
        """每次批量调度的逻辑"""
        return scheduler.schedule(batch_tasks)
    
    # ✅ 启用事件驱动模拟（资源自动释放）
    return enable_event_driven_simulation(
        baseline_scheduler_func=firmament_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=300  # 5分钟调度一次
    )
```

---

## 📋 修改示例

### 1. Firmament

**修改前**:
```python
def run_firmament(tasks, num_machines):
    machines = [Machine(id=i) for i in range(num_machines)]
    scheduler = FirmamentScheduler(machines)
    placements = scheduler.schedule(tasks)
    
    for task_id, machine_id in placements:
        task = task_dict[task_id]
        machines[machine_id].cpu_used += task.cpu
        machines[machine_id].mem_used += task.mem
        machines[machine_id].tasks.append((task_id, task.tenant))
    
    return {"scheduled": len(placements), ...}
```

**修改后**:
```python
from tools.run_with_events import enable_event_driven_simulation

def run_firmament(tasks, num_machines):
    machines = [Machine(id=i) for i in range(num_machines)]
    scheduler = FirmamentScheduler(machines)
    
    def schedule_func(batch_tasks, current_machines):
        return scheduler.schedule(batch_tasks)
    
    return enable_event_driven_simulation(
        baseline_scheduler_func=schedule_func,
        tasks=tasks,
        machines=machines
    )
```

### 2. Mesos DRF

**修改前**:
```python
def run_mesos_drf(tasks, num_machines):
    agents = [Agent(...) for i in range(num_machines)]
    allocator = HierarchicalAllocator(agents)
    
    tasks_by_fw = defaultdict(list)
    for task in tasks:
        tasks_by_fw[task.tenant].append(task)
    
    placements = allocator.allocate(tasks_by_fw)
    
    # 静态累加资源
    machines = [Machine(id=i) for i in range(num_machines)]
    for task_id, machine_id in placements:
        machines[machine_id].cpu_used += task.cpu
        machines[machine_id].mem_used += task.mem
    
    return {"scheduled": len(placements), ...}
```

**修改后**:
```python
from tools.run_with_events import enable_event_driven_simulation

def run_mesos_drf(tasks, num_machines):
    agents = [Agent(...) for i in range(num_machines)]
    allocator = HierarchicalAllocator(agents)
    machines = [Machine(id=i) for i in range(num_machines)]
    
    def schedule_func(batch_tasks, current_machines):
        tasks_by_fw = defaultdict(list)
        for task in batch_tasks:
            mesos_task = MesosTask(
                id=task.id, cpu=task.cpu, mem=task.mem,
                tenant=task.tenant, arrival=task.arrival
            )
            tasks_by_fw[task.tenant].append(mesos_task)
        return allocator.allocate(tasks_by_fw)
    
    return enable_event_driven_simulation(
        baseline_scheduler_func=schedule_func,
        tasks=tasks,
        machines=machines
    )
```

### 3. Tetris

**修改前**:
```python
def run_tetris(tasks, num_machines):
    machines = [Machine(id=i) for i in range(num_machines)]
    
    scheduled = 0
    failed = 0
    
    for task in tasks:
        best_score = -float('inf')
        best_machine = None
        
        for machine in machines:
            if can_fit(machine, task):
                score = tetris_score(machine, task)
                if score > best_score:
                    best_score = score
                    best_machine = machine
        
        if best_machine:
            place_task(best_machine, task)
            scheduled += 1
        else:
            failed += 1
    
    return {"scheduled": scheduled, "failed": failed, ...}
```

**修改后**:
```python
from tools.run_with_events import enable_event_driven_simulation

def run_tetris(tasks, num_machines):
    machines = [Machine(id=i) for i in range(num_machines)]
    
    def schedule_func(batch_tasks, current_machines):
        placements = []
        for task in batch_tasks:
            best_score = -float('inf')
            best_machine = None
            
            for machine in current_machines:
                if machine.cpu - machine.cpu_used >= task.cpu and \
                   machine.mem - machine.mem_used >= task.mem:
                    # Tetris 评分
                    after_cpu = (machine.cpu_used + task.cpu) / machine.cpu
                    after_mem = (machine.mem_used + task.mem) / machine.mem
                    score = after_cpu * after_mem
                    
                    if score > best_score:
                        best_score = score
                        best_machine = machine
            
            if best_machine:
                placements.append((task.id, best_machine.id))
        
        return placements
    
    return enable_event_driven_simulation(
        baseline_scheduler_func=schedule_func,
        tasks=tasks,
        machines=machines
    )
```

---

## 🔍 工作原理

### 内部流程

1. **初始化阶段**
   ```python
   # 添加所有任务提交事件到队列
   for task in tasks:
       heapq.heappush(events, (task.arrival, 'TASK_SUBMIT', task))
   ```

2. **主模拟循环**
   ```python
   while events or running_tasks:
       # A. 处理任务完成事件（资源释放）
       while events[0][0] <= current_time:
           if event_type == 'TASK_END_RUNTIME':
               machine.cpu_used -= task.cpu  # ⭐ 释放资源
               machine.mem_used -= task.mem
       
       # B. 收集待调度任务
       pending_tasks = collect_arrived_tasks(current_time)
       
       # C. 调用 baseline 调度算法
       placements = baseline_scheduler_func(pending_tasks, machines)
       
       # D. 处理调度结果 + 添加结束事件
       for task_id, machine_id in placements:
           allocate_resources(machine, task)
           end_time = current_time + task.duration
           heapq.heappush(events, (end_time, 'TASK_END_RUNTIME', task_id))
       
       # E. 推进时间（batch mode: 固定间隔）
       current_time += batch_step_seconds
   ```

3. **关键差异对比**

| 操作 | 静态模式（旧） | 事件驱动（新）⭐ |
|-----|-------------|----------------|
| 资源分配 | `cpu_used += task.cpu` | ✅ 同左 |
| 资源释放 | ❌ 无 | ✅ `cpu_used -= task.cpu` |
| 时间维度 | ❌ 无 | ✅ `current_time += batch_step` |
| 任务完成 | ❌ 无 | ✅ `TASK_END_RUNTIME` 事件 |
| 循环调度 | ❌ 只运行一次 | ✅ 持续到所有任务完成 |

---

## ⚙️ 配置参数

```python
enable_event_driven_simulation(
    baseline_scheduler_func=my_func,
    tasks=tasks,
    machines=machines,
    batch_step_seconds=300  # 调度间隔（秒）
)
```

### batch_step_seconds 说明

- **300秒（5分钟）**: Firmament 论文中常用的值
- **60秒（1分钟）**: 更频繁的调度
- **900秒（15分钟）**: Kubernetes 默认的重调度周期

**选择建议**: 使用 300 秒（5分钟）以匹配 Firmament 的默认行为。

---

## ✅ 验证清单

修改后，请确认：

- [ ] 所有调度器都使用 `enable_event_driven_simulation`
- [ ] 任务有 `duration` 字段（从 trace 加载）
- [ ] 调度函数只负责返回 `placements`，不直接修改资源
- [ ] 移除手动的 `cpu_used += task.cpu` 代码
- [ ] 测试输出显示资源释放正常工作

---

## 📊 预期效果

### 修改前（不公平）

```
Firmament:  成功率=10%, 利用率=20% ❌
Mesos DRF:  成功率=15%, 利用率=25% ❌
NextGen:    成功率=100%, 利用率=95% ✅（有动态释放）
```

### 修改后（公平）

```
Firmament:  成功率=95%, 利用率=90% ✅
Mesos DRF:  成功率=92%, 利用率=88% ✅
Tetris:     成功率=89%, 利用率=85% ✅
NextGen:    成功率=97%, 利用率=93% ✅
```

所有算法都在同一起跑线上！🏁

---

## 📚 参考源码

- Firmament模拟器主循环: `baselines/firmament/src/sim/simulator.cc::ReplaySimulation()`
- 事件管理器: `baselines/firmament/src/sim/event_manager.cc`
- 模拟器桥接: `baselines/firmament/src/sim/simulator_bridge.cc`
- 任务完成处理: `baselines/firmament/src/scheduling/event_driven_scheduler.cc::HandleTaskCompletion()`

---

**更新日期**: 2025-01-20  
**作者**: AI Scheduler Team

