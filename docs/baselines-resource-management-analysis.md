# 🔍 Baseline 算法资源管理与重试机制分析

## 📋 摘要

通过深入分析 **Firmament (OSDI'16)** 和 **Mesos DRF (NSDI'11)** 的源码，我们发现：

1. ✅ **两者都有完善的资源释放机制**
2. ✅ **两者都支持任务失败后的重试**
3. ❌ **我们的模拟实现与真实系统存在重大差异**

---

## 1️⃣ Firmament 资源管理机制

### 源码位置
- `baselines/firmament/src/scheduling/flow/flow_scheduler.cc`
- `baselines/firmament/src/scheduling/event_driven_scheduler.cc`

### 核心函数

#### 1.1 任务完成 - 资源释放
```cpp
void FlowScheduler::HandleTaskCompletion(TaskDescriptor* td_ptr,
                                         TaskFinalReport* report) {
  // 1. 调用父类处理（EventDrivenScheduler）
  EventDrivenScheduler::HandleTaskCompletion(td_ptr, report);
  
  // 2. 从 Flow Graph 中移除任务节点
  uint64_t task_node_id = flow_graph_manager_->TaskCompleted(td_ptr->uid());
  tasks_completed_during_solver_run_.insert(task_node_id);
}
```

父类实现：
```cpp
void EventDrivenScheduler::HandleTaskCompletion(TaskDescriptor* td_ptr,
                                                TaskFinalReport* report) {
  // 1. 找到任务绑定的资源
  ResourceID_t* res_id_ptr = BoundResourceForTask(td_ptr->uid());
  ResourceStatus* rs_ptr = FindPtrOrNull(*resource_map_, res_id_tmp);
  
  // 2. ⭐ 解绑任务并释放资源
  CHECK(UnbindTaskFromResource(td_ptr, res_id_tmp));
  
  // 3. 通知 executor 处理任务完成
  exec->HandleTaskCompletion(td_ptr, report);
}
```

#### 1.2 资源解绑 - 关键实现
```cpp
bool EventDrivenScheduler::UnbindTaskFromResource(TaskDescriptor* td_ptr,
                                                   ResourceID_t res_id) {
  TaskID_t task_id = td_ptr->uid();
  
  // 1. 获取资源状态
  ResourceStatus* rs_ptr = FindPtrOrNull(*resource_map_, res_id);
  ResourceDescriptor* rd_ptr = rs_ptr->mutable_descriptor();
  
  // 2. ⭐ 设置资源为空闲状态
  if (rd_ptr->current_running_tasks_size() == 0) {
    rd_ptr->set_state(ResourceDescriptor::RESOURCE_IDLE);
  }
  
  // 3. 从任务绑定表中移除
  task_bindings_.erase(task_id);
  
  return true;
}
```

### 1.3 任务驱逐（Eviction）- 支持重调度
```cpp
void EventDrivenScheduler::HandleTaskEviction(TaskDescriptor* td_ptr,
                                              ResourceDescriptor* rd_ptr) {
  ResourceID_t res_id = ResourceIDFromString(rd_ptr->uuid());
  
  // 1. ⭐ 解绑并释放资源
  CHECK(UnbindTaskFromResource(td_ptr, res_id));
  
  // 2. ⭐ 任务标记为 RUNNABLE，重新加入调度队列
  td_ptr->set_state(TaskDescriptor::RUNNABLE);
  InsertTaskIntoRunnables(JobIDFromString(td_ptr->job_id()), td_ptr->uid());
  
  // 3. 通知 executor 处理驱逐
  exec->HandleTaskEviction(td_ptr);
}
```

### 1.4 任务失败 - 自动重试
```cpp
void EventDrivenScheduler::HandleTaskFailure(TaskDescriptor* td_ptr) {
  // 1. 解绑资源
  CHECK(UnbindTaskFromResource(td_ptr, res_id_tmp));
  
  // 2. 设置为失败状态
  td_ptr->set_state(TaskDescriptor::FAILED);
  
  // 3. ⭐ 如果未超过重试限制，重新调度
  if (!exceeded_retry_limit) {
    scheduler::SchedulerStats scheduler_stats;
    ScheduleJob(jd, &scheduler_stats);  // 重新调度
  }
}
```

### 1.5 节点故障处理
```cpp
void FlowScheduler::HandleTasksFromDeregisteredResource(
    ResourceTopologyNodeDescriptor* rtnd_ptr) {
  vector<TaskID_t> tasks = BoundTasksForResource(res_id);
  
  for (auto& task_id : tasks) {
    if (FLAGS_reschedule_tasks_upon_node_failure) {
      // ⭐ 重新调度失败节点上的任务
      HandleTaskEviction(td_ptr, rd_ptr);
    } else {
      HandleTaskFailure(td_ptr);
    }
  }
}
```

---

## 2️⃣ Mesos DRF 资源管理机制

### 源码位置
- `baselines/mesos/src/master/allocator/mesos/hierarchical.cpp`
- `baselines/mesos/src/master/master.cpp`

### 核心函数

#### 2.1 资源回收 - `recoverResources()`
```cpp
void HierarchicalAllocatorProcess::recoverResources(
    const FrameworkID& frameworkId,
    const SlaveID& slaveId,
    const Resources& resources,
    const Option<Filters>& filters,
    bool isAllocated)
{
  // 1. 检查资源是否为空
  if (resources.empty()) {
    return;
  }
  
  // 2. ⭐ 更新 slave 的已分配资源统计
  if (isAllocated && slave.isSome()) {
    (*slave)->totalAllocated -= resources;
    roleTree.untrackAllocated(slaveId, resources);
  }
  
  // 3. ⭐ 增加 slave 的可用资源
  (*slave)->increaseAvailable(frameworkId, resources);
  
  VLOG(1) << "Recovered " << resources 
          << " on agent " << slaveId 
          << " from framework " << frameworkId;
  
  // 4. ⭐ 更新 role sorter（DRF 核心）
  Sorter* frameworkSorter = CHECK_NOTNONE(getFrameworkSorter(role));
  if (frameworkSorter->contains(frameworkId.value())) {
    untrackAllocatedResources(slaveId, frameworkId, resources);
  }
  
  // 5. 可选：安装过滤器（防止立即重新分配）
  if (filters.isSome() && timeout.get() != Duration::zero()) {
    // 创建 RefusedOfferFilter
    // 在 timeout 后过期
  }
}
```

#### 2.2 资源分配跟踪
```cpp
void HierarchicalAllocatorProcess::transitionOfferedToAllocated(
    const SlaveID& slaveId,
    const Resources& resources)
{
  // 从 "offered" 转为 "allocated"
  CHECK_NOTNONE(getSlave(slaveId))->totalAllocated += resources;
  roleTree.trackAllocated(slaveId, resources);
}
```

#### 2.3 取消分配跟踪
```cpp
void HierarchicalAllocatorProcess::untrackAllocatedResources(
    const SlaveID& slaveId,
    const FrameworkID& frameworkId,
    const Resources& allocated)
{
  // 从各个 sorter 中移除分配记录
  foreachpair (const string& role,
               const Resources& allocation,
               allocated.allocations()) {
    Sorter* frameworkSorter = CHECK_NOTNONE(getFrameworkSorter(role));
    
    // ⭐ 从 DRF sorter 中移除
    frameworkSorter->unallocated(
        frameworkId.value(), slaveId, allocation);
    
    // ⭐ 从 role sorter 中移除
    roleSorter->unallocated(role, slaveId, allocation);
  }
}
```

### 2.4 Mesos Master 的任务状态管理

任务完成时的调用链：
```cpp
Master::statusUpdate()
  -> Framework::update()
  -> allocator->recoverResources()  // ⭐ 释放资源
```

任务失败重试：
```cpp
Master::_launchTasks()
  -> if (task_launch_failed) {
       // ⭐ 资源立即回收
       allocator->recoverResources(frameworkId, slaveId, resources);
       
       // ⭐ 通知 framework 任务失败
       framework->taskLost(taskId, slaveId, REASON_TASK_INVALID);
     }
```

---

## 3️⃣ 我们的实现 vs 真实系统

### ❌ **问题 1：无资源释放**

#### 我们的实现
```python
def run_mesos_drf(tasks, num_machines):
    # ...
    for task_id, machine_id in placements:
        machines[machine_id].cpu_used += task.cpu  # ⭐ 只增加，不减少
        machines[machine_id].mem_used += task.mem
        machines[machine_id].tasks.append((task_id, task.tenant))
    
    # ❌ 没有资源释放逻辑
    # ❌ 任务永久占用资源
    return {"scheduled": len(placements), ...}
```

#### 真实系统
```cpp
// 任务完成时
recoverResources(frameworkId, slaveId, resources)
  -> slave->increaseAvailable(resources);      // ⭐ 增加可用资源
  -> slave->totalAllocated -= resources;       // ⭐ 减少已分配
  -> frameworkSorter->unallocated(...);        // ⭐ 更新 DRF 状态
```

### ❌ **问题 2：无重试机制**

#### 我们的实现
```python
def run_tetris(tasks, num_machines):
    for task in tasks:
        placed = False
        for machine in machines:
            if can_fit(machine, task):
                place_task(machine, task)
                placed = True
                scheduled += 1
                break
        
        if not placed:
            failed += 1  # ❌ 直接标记失败，不重试
    
    return {"failed": failed, ...}
```

#### 真实系统
```cpp
// Firmament
void HandleTaskEviction(task) {
  UnbindTaskFromResource(task);
  task->set_state(RUNNABLE);
  InsertTaskIntoRunnables(task);  // ⭐ 重新加入调度队列
}

// Mesos
Master::_statusUpdate() {
  if (status == TASK_FAILED && retries < MAX_RETRIES) {
    // ⭐ 重新提交任务
    framework->resubmitTask(taskId);
  }
}
```

### ❌ **问题 3：静态快照 vs 动态事件驱动**

#### 我们的实现
```python
# 一次性调度所有任务（静态快照）
placements = allocator.allocate(all_tasks)

# ❌ 没有时间维度
# ❌ 没有任务完成事件
# ❌ 没有资源回收周期
```

#### 真实系统
```cpp
// 事件驱动架构
class EventDrivenScheduler {
  // 持续监听事件
  void HandleTaskCompletion(task) { ... }
  void HandleTaskEviction(task) { ... }
  void HandleTaskFailure(task) { ... }
  void HandleNodeFailure(node) { ... }
  
  // ⭐ 周期性重新调度
  void ScheduleAllJobs() { 
    while (有 runnable 任务) {
      分配资源 -> 运行任务 -> 等待完成 -> 释放资源
    }
  }
}
```

---

## 4️⃣ 对比表格

| 特性 | Firmament (真实) | Mesos (真实) | 我们的模拟 ❌ |
|------|-----------------|-------------|------------|
| **资源释放** | ✅ `UnbindTaskFromResource()` | ✅ `recoverResources()` | ❌ 无 |
| **任务完成事件** | ✅ `HandleTaskCompletion()` | ✅ `statusUpdate()` | ❌ 无 |
| **失败重试** | ✅ 重新加入 `runnable_tasks_` | ✅ `resubmitTask()` | ❌ 无 |
| **驱逐重调度** | ✅ `HandleTaskEviction()` | ✅ 资源立即回收 | ❌ 无 |
| **节点故障处理** | ✅ `HandleTasksFromDeregisteredResource()` | ✅ `taskLost()` | ❌ 无 |
| **时间维度** | ✅ 事件驱动 + 周期调度 | ✅ 异步消息驱动 | ❌ 静态快照 |
| **DRF 状态更新** | ✅ Flow graph 动态更新 | ✅ `Sorter::allocated/unallocated()` | ❌ 一次性计算 |
| **资源过滤器** | ✅ 支持（避免立即重分配） | ✅ `RefusedOfferFilter` | ❌ 无 |
| **优先级抢占** | ✅ `HandleTaskEviction()` | ✅ Offer 拒绝机制 | ❌ 无 |

---

## 5️⃣ 影响分析

### 对实验结果的影响

#### 1. **资源利用率严重低估**
```python
# 我们的模拟
调度 1000 任务 -> 资源永久占用 -> 后续任务失败 -> 利用率 50%

# 真实系统
调度 1000 任务 -> 运行 + 完成 -> 资源释放 -> 继续调度 -> 利用率 90%+
```

#### 2. **调度成功率不公平**
- **NextGen**: 有动态资源释放 → 成功率高 ✅
- **Firmament/Mesos**: 无资源释放 → 成功率低 ❌
- **比较不公平**：NextGen 有额外优势

#### 3. **无法模拟长时间运行**
```python
# 真实集群运行 7 天
Day 1: 调度 + 运行 + 释放 -> 循环利用资源
Day 7: 仍在正常运行

# 我们的模拟
前 10 分钟: 调度成功
后面时间: 资源耗尽，无法继续
```

---

## 6️⃣ 修复建议

### 方案 A：为所有算法添加动态资源管理 ✅ **推荐**

```python
class UnifiedDynamicScheduler:
    def run_with_dynamic_release(self, algorithm_func, tasks, machines):
        current_time = 0
        active_tasks = []  # (task_id, end_time, machine_id, resources)
        
        while has_pending_tasks or has_active_tasks:
            # 1. ⭐ 释放已完成任务
            for task in active_tasks:
                if task.end_time <= current_time:
                    release_resources(task)
                    active_tasks.remove(task)
            
            # 2. 运行调度算法
            placements = algorithm_func(pending_tasks, machines)
            
            # 3. 记录任务结束时间
            for task_id, machine_id in placements:
                end_time = current_time + task.duration
                active_tasks.append((task_id, end_time, machine_id, ...))
            
            # 4. 推进时间
            current_time = next_event_time()
```

### 方案 B：为所有算法添加重试机制

```python
def schedule_with_retry(tasks, machines, max_retries=3):
    pending = deque(tasks)
    retry_queue = []
    
    while pending or retry_queue:
        task = pending.popleft() if pending else retry_queue.pop(0)
        
        if place_task(task, machines):
            scheduled.append(task)
        else:
            if task.retries < max_retries:
                task.retries += 1
                retry_queue.append(task)
            else:
                failed.append(task)
```

### 方案 C：使用真实 trace 的时间戳

```python
# 从 batch_instance.csv 读取
task.start_time  = row[5]   # 真实开始时间
task.end_time    = row[6]   # 真实结束时间
task.duration    = end_time - start_time

# 按时间顺序模拟
for timestamp in sorted(all_timestamps):
    release_completed_tasks(timestamp)
    schedule_new_arrivals(timestamp)
```

---

## 7️⃣ 结论

### ✅ 真实系统的特点

1. **动态事件驱动**：任务完成 → 资源释放 → 继续调度
2. **完善的重试机制**：失败/驱逐 → 重新加入队列 → 再次调度
3. **时间维度**：持续运行，资源不断循环利用
4. **状态维护**：实时更新 DRF dominant share、Flow graph

### ❌ 我们模拟的问题

1. **静态快照**：一次性分配所有任务
2. **无资源释放**：任务永久占用资源
3. **无重试机制**：失败即失败
4. **不公平比较**：只有 NextGen 有动态管理

### 🎯 下一步行动

**建议立即实施方案 A + C**：
- ✅ 已为 NextGen 实现动态资源管理
- ⚠️ **需要为 Firmament、Mesos、Tetris 也实现相同机制**
- ⚠️ 使用真实的任务时长数据
- ⚠️ 统一所有算法的评估标准

这样才能进行**公平、有意义的对比**！

---

**参考源码**:
- Firmament: `baselines/firmament/src/scheduling/event_driven_scheduler.cc`
- Mesos: `baselines/mesos/src/master/allocator/mesos/hierarchical.cpp`
- 我们的实现: `tools/run_complete_comparison.py`


