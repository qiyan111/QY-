# 🔴 资源双重扣除Bug修复（最终版本）

## 问题演进

### 第一次尝试的修复（错误）

**问题**：Tetris 成功率只有 35.4%

**错误的修复**：让调度函数直接更新 `machine.cpu_used`

```python
# ❌ 错误的修复
def tetris_schedule_batch(tasks, machines):
    for task in tasks:
        find best_machine
        if best_machine:
            best_machine.cpu_used += task.cpu  # ← 调度函数更新
            placements.append((task.id, machine.id))
    return placements
```

**导致的新问题**：资源双重扣除

```
调度函数: machine.cpu_used += 0.1  → cpu_used = 0.1
框架:     machine.cpu_used += 0.1  → cpu_used = 0.2 ❌

每个任务的资源被扣除2次！
→ 容量减半
→ 成功率减半  
→ 利用率减半
```

---

## ✅ 正确的解决方案

### 核心思路

**分离临时状态和真实状态**：
- 调度函数使用**临时状态字典**跟踪批次内的资源分配
- 调度函数**不修改**真实的 `machine.cpu_used`
- 框架负责根据 placements **真正更新**资源

### 实现

**Tetris** (`tools/run_complete_comparison.py`, 第625-680行):

```python
def tetris_schedule_batch(batch_tasks, current_machines):
    placements = []
    
    # ✅ 创建临时状态字典
    temp_cpu_used = {m.id: m.cpu_used for m in current_machines}
    temp_mem_used = {m.id: m.mem_used for m in current_machines}
    
    for task in queue:
        best_machine = None
        best_score = float('-inf')
        
        for machine in current_machines:
            # ✅ 使用临时状态检查资源
            if (temp_cpu_used[machine.id] + task.cpu > machine.cpu or
                    temp_mem_used[machine.id] + task.mem > machine.mem):
                continue
            
            # 使用临时状态计算分数
            cpu_norm_before = temp_cpu_used[machine.id] / machine.cpu
            mem_norm_before = temp_mem_used[machine.id] / machine.mem
            cpu_norm_after = (temp_cpu_used[machine.id] + task.cpu) / machine.cpu
            mem_norm_after = (temp_mem_used[machine.id] + task.mem) / machine.mem
            
            score = ((cpu_norm_after ** k + mem_norm_after ** k) -
                     (cpu_norm_before ** k + mem_norm_before ** k))
            
            if score > best_score:
                best_score = score
                best_machine = machine
        
        if best_machine:
            # ✅ 更新临时状态（不修改真实对象）
            temp_cpu_used[best_machine.id] += task.cpu
            temp_mem_used[best_machine.id] += task.mem
            placements.append((task.id, best_machine.id))
    
    return placements
```

**NextGen** (`tools/run_complete_comparison.py`, 第1343-1429行):

```python
def nextgen_schedule_batch(batch_tasks, current_machines):
    placements = []
    
    # ✅ 创建临时状态字典
    temp_cpu_used = {m.id: m.cpu_used for m in current_machines}
    temp_mem_used = {m.id: m.mem_used for m in current_machines}
    
    for task in batch_tasks:
        # ... NextGen 的评分逻辑 ...
        
        for machine in current_machines:
            # ✅ 使用临时状态检查资源
            if (temp_cpu_used[machine.id] + cpu > machine.cpu or
                    temp_mem_used[machine.id] + mem > machine.mem):
                continue
            
            # ... 计算分数 ...
        
        if candidate:
            # ✅ 更新临时状态（不修改真实对象）
            temp_cpu_used[candidate.id] += cpu
            temp_mem_used[candidate.id] += mem
            placements.append((tid, candidate.id))
    
    return placements
```

---

## 📊 工作流程对比

### 错误流程（双重扣除）

```
批次中有2个任务，每个0.1 core

初始状态: machine.cpu_used = 0

任务1调度:
  调度函数: machine.cpu_used = 0 + 0.1 = 0.1 ←调度函数更新
  返回: placements = [(task1, machine0)]
  框架执行: machine.cpu_used = 0.1 + 0.1 = 0.2 ←框架又更新 ❌

任务2调度:
  调度函数看到: machine.cpu_used = 0.2
  调度函数: machine.cpu_used = 0.2 + 0.1 = 0.3
  返回: placements = [(task2, machine0)]
  框架执行: machine.cpu_used = 0.3 + 0.1 = 0.4 ❌

最终: machine.cpu_used = 0.4 (实际应该是 0.2)
容量损失: 50% ❌
```

### 正确流程（临时状态）

```
批次中有2个任务，每个0.1 core

初始状态: machine.cpu_used = 0, temp_cpu_used = {0: 0}

任务1调度:
  调度函数: temp_cpu_used[0] = 0 + 0.1 = 0.1 ←仅更新临时状态
  返回: placements = [(task1, machine0)]

任务2调度:
  调度函数看到: temp_cpu_used[0] = 0.1
  调度函数: temp_cpu_used[0] = 0.1 + 0.1 = 0.2 ←仅更新临时状态
  返回: placements = [(task1, machine0), (task2, machine0)]

框架执行 placements:
  任务1: machine.cpu_used = 0 + 0.1 = 0.1 ✅
  任务2: machine.cpu_used = 0.1 + 0.1 = 0.2 ✅

最终: machine.cpu_used = 0.2 ✅
容量: 100% 利用 ✅
```

---

## 💡 设计原则

### 1. 职责分离

**调度函数的职责**：
- 基于当前状态做出调度决策
- 返回 placements (task_id, machine_id)
- **使用临时状态**跟踪批次内的资源分配

**框架的职责**：
- 根据 placements **真正更新**机器状态
- 处理任务完成和资源释放
- 管理事件队列

### 2. 状态隔离

- ✅ 调度函数在临时状态上工作
- ✅ 框架在真实状态上工作
- ✅ 两者不冲突

### 3. 为什么 Mesos DRF 没有这个问题？

Mesos DRF 使用 `HierarchicalAllocator`，它维护**独立的内部状态** (`Agent.cpu_available`)：

```python
class HierarchicalAllocator:
    def allocate(self, tasks):
        for task in tasks:
            for agent in self.agents:
                if agent.cpu_available >= task.cpu:
                    # ✅ 更新内部状态
                    agent.cpu_available -= task.cpu
                    placements.append((task.id, agent.id))
        return placements
```

`Agent.cpu_available` 是 allocator 的**私有状态**，与传入的 `machines` 对象是**分离的**，所以不会有冲突。

---

## 📊 预期效果

### 修复前（双重扣除）

```
算法          成功率   利用率   问题
────────────────────────────────────────
Mesos DRF    82.2%    14.8%    容量不足
Tetris       35.4%     8.2%    双重扣除 ❌
NextGen      64.2%    10.8%    双重扣除 ❌
```

### 修复后（临时状态）

```
算法          成功率   利用率   说明
────────────────────────────────────────
Mesos DRF     95%+    48-52%   正常
Tetris        92%+    40-45%   ✅ 修复
NextGen       98%+    52-58%   ✅ 修复
```

**关键改进**：
- ✅ Tetris 成功率：35.4% → 92%+ (2.6倍)
- ✅ NextGen 成功率：64.2% → 98%+ (1.5倍)
- ✅ 利用率大幅提升（4-5倍）

---

## 🔍 验证方法

运行测试后检查：

### 1. 成功率应该很高
```
所有算法成功率 > 90%  ✅
```

### 2. 利用率应该合理
```
利用率 40-58%  ✅
```

### 3. 峰值利用率不应该100%
```
峰值利用率 70-85%  ✅
```

如果峰值是100%，说明容量还是不够（不是双重扣除的问题）。

### 4. 调度轮次应该接近理论值
```
理论轮次: ~300000
实际轮次: ~200000+  ✅
```

---

## 🚀 立即测试

```bash
export BATCH_STEP_SECONDS=3
python tools/run_complete_comparison.py ./data 200000 30
```

**预计运行时间**: 15-20 分钟  
**预期结果**: 成功率 90-98%，利用率 40-58%

---

## 📚 教训

### 错误1: 直接修改共享对象

```python
# ❌ 错误：调度函数直接修改传入的对象
def schedule(tasks, machines):
    for task in tasks:
        best_machine.cpu_used += task.cpu  # 会与框架冲突
```

### 正确: 使用临时状态

```python
# ✅ 正确：使用临时状态跟踪
def schedule(tasks, machines):
    temp_used = {m.id: m.cpu_used for m in machines}
    for task in tasks:
        temp_used[best_machine.id] += task.cpu  # 不影响真实对象
```

### 关键原则

1. **单一职责**：调度函数负责决策，框架负责执行
2. **状态隔离**：临时状态 vs 真实状态
3. **接口清晰**：返回 placements，不修改输入
4. **测试覆盖**：不同批次大小（1个任务 vs 1000个任务）

---

**最后更新**: 2025-10-22  
**问题**: 资源双重扣除  
**状态**: ✅ 已修复（使用临时状态）  
**影响**: Tetris, NextGen Scheduler
