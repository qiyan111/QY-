#!/usr/bin/env python3
"""
统一的事件驱动模拟包装器
严格按照 Firmament simulator.cc 的批量模式实现

使用方法：
    from tools.run_with_events import enable_event_driven_simulation
    
    result = enable_event_driven_simulation(
        baseline_scheduler_func=my_scheduler,
        tasks=tasks,
        machines=machines
    )
"""
from __future__ import annotations
import heapq
import os
from typing import List, Dict, Callable, Any


def enable_event_driven_simulation(
    baseline_scheduler_func: Callable,
    tasks: List[Any],
    machines: List[Any],
    batch_step_seconds: int = 300,  # 5分钟调度一次（模仿 Firmament 默认值）
    scheduler_obj: Any = None,  # 调度器对象（用于调用 task_completed 等方法）
    allocator_obj: Any = None,  # Allocator 对象（用于调用 recover_resources）
) -> Dict:
    """
    为任何baseline调度算法启用事件驱动模拟
    
    严格按照 firmament/src/sim/simulator.cc::ReplaySimulation() 的逻辑：
    1. 周期性运行调度器（batch mode）
    2. 每次调度前处理任务完成事件（资源释放）
    3. 任务放置时添加结束事件（基于 duration）
    
    Args:
        baseline_scheduler_func: 调度函数，签名为 func(tasks, machines) -> placements
        tasks: 任务列表（需要有 duration 字段）
        machines: 机器列表
        batch_step_seconds: 调度间隔（秒）
    
    Returns:
        包含 scheduled/failed/machines 的结果字典
    """
    # 事件队列 (timestamp, counter, event_type, data)
    # counter 用于在时间戳相同时保证唯一性，避免比较 Task 对象
    events = []
    event_counter = 0
    
    # 添加所有任务提交事件
    for task in tasks:
        heapq.heappush(events, (task.arrival, event_counter, 'TASK_SUBMIT', task))
        event_counter += 1
    
    print(f"  [事件队列] 初始化: {len(tasks)} 个任务提交事件")
    if events:
        print(f"  [事件队列] 第一个事件时间: {events[0][0]}, 最后事件时间: {max(events)[0]}")
    
    # 跟踪运行中的任务 {task_id: (machine_id, end_time, resources)}
    running_tasks = {}
    task_dict = {t.id: t for t in tasks}
    
    # ⭐ 追踪所有已调度任务（用于计算 effective_util）
    all_scheduled_tasks = []  # 存储所有已调度任务的 ID
    
    # 统计
    scheduled_count = 0
    failed_count = 0
    
    # 当前模拟时间与调度节拍（⭐ 从第一个任务到达时间开始）
    start_time = min(t.arrival for t in tasks) if tasks else 0
    current_time = start_time
    next_schedule_at = start_time
    num_scheduling_rounds = 0
    # 允许通过环境变量调整最大轮次，避免大规模 trace 被过早截断
    max_scheduling_rounds = int(os.getenv("MAX_SCHED_ROUNDS", "1000000"))
    
    print(f"  [模拟] 开始时间: {current_time}, 最大轮次: {max_scheduling_rounds}")
    
    # 待调度任务缓冲区（在调度节拍之间持续累积）
    pending_tasks = []
    
    # ⭐ 追踪过程中的利用率（解决"最终快照"问题）
    util_samples = []  # 每个调度轮次的利用率快照（调试用）
    cpu_util_samples = []  # CPU 利用率快照
    mem_util_samples = []  # MEM 利用率快照
    real_cpu_samples = []  # 真实 CPU 使用量快照
    cv_samples = []       # 节点利用率变异系数快照（不均衡度）
    max_util_seen = 0.0
    # 时间加权积分（AUC）
    tw_util_sum = 0.0
    tw_cpu_sum = 0.0
    tw_mem_sum = 0.0
    tw_real_cpu_sum = 0.0
    tw_cv_sum = 0.0
    tw_total = 0.0
    
    # ========== 主模拟循环（对应 Firmament 的 ReplaySimulation while 循环）==========
    debug_round = 0
    while events or running_tasks or pending_tasks:
        if num_scheduling_rounds >= max_scheduling_rounds:
            print(f"  [循环] 达到最大调度轮次限制: {max_scheduling_rounds}")
            break

        # 如果没有任何负载且无待调度任务，则将 next_schedule_at 对齐到下一个事件时间以避免空转
        if not running_tasks and not pending_tasks and events:
            next_schedule_at = max(next_schedule_at, events[0][0])
            current_time = next_schedule_at

        # ⭐ 调试：每1000轮输出一次状态（减少刷屏）
        if os.getenv("DEBUG_EVENT_LOOP", "0") == "1" and debug_round == 0 and num_scheduling_rounds % 1000 == 0:
            print(f"  [循环 {num_scheduling_rounds}] schedule_at={next_schedule_at}, "
                  f"events={len(events)}, running={len(running_tasks)}, pending={len(pending_tasks)}")
            if events:
                print(f"              next_event_time={events[0][0]}")
            debug_round = 1000
        debug_round -= 1

        # ========== 步骤 1: 处理所有 <= next_schedule_at 的事件 ==========
        # 对应 bridge->ProcessSimulatorEvents(run_scheduler_at)
        events_processed = 0
        while events and events[0][0] <= next_schedule_at:
            timestamp, _, event_type, data = heapq.heappop(events)
            events_processed += 1
            
            if event_type == 'TASK_END_RUNTIME':
                # ⭐ 任务完成 -> 释放资源（对应 TaskCompleted -> HandleTaskCompletion -> UnbindTaskFromResource）
                task_id = data
                if task_id in running_tasks:
                    machine_id, _, resources = running_tasks.pop(task_id)
                    machine = machines[machine_id]
                    
                    # 1. 释放机器资源（对应 UnbindTaskFromResource）
                    machine.cpu_used = max(0, machine.cpu_used - resources['cpu'])
                    machine.mem_used = max(0, machine.mem_used - resources['mem'])
                    
                    # 2. ⭐ 调用调度器的任务完成处理（严格按源码）
                    if scheduler_obj and hasattr(scheduler_obj, 'task_completed'):
                        # Firmament: flow_graph_manager_->TaskCompleted(task_id)
                        scheduler_obj.task_completed(task_id)
                    
                    # 3. ⭐ 调用 allocator 的资源回收（严格按源码）
                    if allocator_obj and hasattr(allocator_obj, 'recover_resources'):
                        # Mesos: allocator->recoverResources(framework_id, agent_id, resources)
                        framework_id = resources.get('tenant', resources.get('framework_id', ''))
                        allocator_obj.recover_resources(framework_id, machine_id, 
                                                       resources['cpu'], resources['mem'])
            
            elif event_type == 'TASK_SUBMIT':
                # 任务到达，加入待调度队列（累积到下一次调度）
                pending_tasks.append(data)
        
        # ⭐ 调试：如果处理了很多事件但没有待调度任务，说明有问题
        if os.getenv("DEBUG_EVENT_LOOP", "0") == "1" and events_processed > 0 and num_scheduling_rounds < 10:
            print(f"  [步骤1] 处理了 {events_processed} 个事件, pending_tasks={len(pending_tasks)}")
        
        # ========== 步骤 2: 运行调度器（仅在调度节拍触发时运行） ==========
        # 对应 ScheduleJobsHelper
        if pending_tasks:
            # ⭐ 调试：第一轮调度
            if os.getenv("DEBUG_EVENT_LOOP", "0") == "1" and num_scheduling_rounds < 3:
                print(f"  [步骤2] 调度轮次 {num_scheduling_rounds}: 尝试调度 {len(pending_tasks)} 个任务")
            
            # 调用baseline调度算法
            try:
                placements = baseline_scheduler_func(pending_tasks, machines)
            except Exception as e:
                print(f"  ⚠️  调度器错误: {e}")
                import traceback
                traceback.print_exc()
                placements = []
            
            # ⭐ 调试：调度结果
            if os.getenv("DEBUG_EVENT_LOOP", "0") == "1" and num_scheduling_rounds < 3:
                print(f"  [步骤2] 调度器返回 {len(placements) if placements else 0} 个placement")
            
            # 处理调度结果
            scheduled_ids = set()
            for task_id, machine_id in placements:
                task = task_dict.get(task_id)
                if not task:
                    continue
                
                machine = machines[machine_id]
                
                # 检查资源是否足够（二次确认）
                if machine.cpu - machine.cpu_used < task.cpu or \
                   machine.mem - machine.mem_used < task.mem:
                    continue
                
                # 占用资源
                machine.cpu_used += task.cpu
                machine.mem_used += task.mem
                machine.tasks.append((task_id, task.tenant))
                
                scheduled_ids.add(task_id)
                scheduled_count += 1
                all_scheduled_tasks.append(task_id)  # ⭐ 记录所有已调度任务
                
                # ⭐ 添加任务结束事件（对应 OnTaskPlacement -> UpdateTaskEndEvents）
                if hasattr(task, 'duration') and task.duration > 0:
                    end_time = current_time + task.duration
                    heapq.heappush(events, (end_time, event_counter, 'TASK_END_RUNTIME', task_id))
                    event_counter += 1
                    
                    # 跟踪运行中的任务（包含 tenant 信息用于 recover_resources）
                    running_tasks[task_id] = (machine_id, end_time, {
                        'cpu': task.cpu, 
                        'mem': task.mem,
                        'tenant': task.tenant if hasattr(task, 'tenant') else '',
                        'framework_id': task.tenant if hasattr(task, 'tenant') else '',
                    })
            
            # 未调度任务不判定为失败，保留到下一轮（与真实系统一致）
            # 只有在模拟结束仍未被调度时，才可作为失败统计（本模拟暂不计算该项）
            pending_tasks = [t for t in pending_tasks if t.id not in scheduled_ids]
        
        # ========== 步骤 3: 采样当前利用率（用于计算平均/峰值）==========
        have_load = running_tasks or any((m.cpu_used > 0 or m.mem_used > 0) for m in machines)
        avg_util_now = cpu_util_now = mem_util_now = real_cpu_now = cv_now = 0.0
        if have_load:
            current_utils = [max(m.cpu_used/m.cpu if m.cpu > 0 else 0,
                                 m.mem_used/m.mem if m.mem > 0 else 0) for m in machines]
            cpu_utils = [m.cpu_used/m.cpu if m.cpu > 0 else 0 for m in machines]
            mem_utils = [m.mem_used/m.mem if m.mem > 0 else 0 for m in machines]

            # ⭐ 计算当前时刻运行中任务的真实CPU使用量
            real_cpu_now = 0.0
            for tid, (_mid, _end_time, res) in running_tasks.items():
                task = task_dict.get(tid)
                if task:
                    real_cpu_now += getattr(task, 'real_cpu', res['cpu'] * 0.5)

            if current_utils:
                avg_util_now = sum(current_utils) / len(current_utils)
                max_util_now = max(current_utils)
                cpu_util_now = sum(cpu_utils) / len(cpu_utils) if cpu_utils else 0.0
                mem_util_now = sum(mem_utils) / len(mem_utils) if mem_utils else 0.0
                # 变异系数（不均衡度）
                mean_u = avg_util_now
                if mean_u > 1e-12:
                    var = sum((u - mean_u) ** 2 for u in current_utils) / len(current_utils)
                    std_u = var ** 0.5
                    cv_now = std_u / mean_u
                else:
                    cv_now = 0.0

                util_samples.append(avg_util_now)
                cpu_util_samples.append(cpu_util_now)
                mem_util_samples.append(mem_util_now)
                real_cpu_samples.append(real_cpu_now)  # ⭐ 采样真实CPU使用量
                cv_samples.append(cv_now)
                max_util_seen = max(max_util_seen, max_util_now)

        # ========== 步骤 4: 推进到下一个调度节拍（并做时间加权积分） ==========
        # 以区间长度做时间加权： [current_time, next_schedule_at)
        next_time = next_schedule_at + batch_step_seconds

        # 时间加权积分（使用本轮采样的状态覆盖 [current_time, next_time)）
        if have_load:
            delta_t = max(0, (next_time - next_schedule_at))
            if delta_t > 0:
                tw_total += delta_t
                tw_util_sum += avg_util_now * delta_t
                tw_cpu_sum += cpu_util_now * delta_t
                tw_mem_sum += mem_util_now * delta_t
                tw_real_cpu_sum += real_cpu_now * delta_t
                tw_cv_sum += cv_now * delta_t

        current_time = next_time
        next_schedule_at = next_time
        
        num_scheduling_rounds += 1
        
        # 如果没有更多事件且没有运行中的任务且无待调度任务，提前结束
        if not events and not running_tasks and not pending_tasks:
            break
    
    # 最终统计
    # ⭐ 计算时间加权均值（若无时间窗口，则退化为算术平均）
    if tw_total > 0:
        avg_util_over_time = tw_util_sum / tw_total
        avg_cpu_util = tw_cpu_sum / tw_total
        avg_mem_util = tw_mem_sum / tw_total
        capacity_total = sum(m.cpu for m in machines)
        avg_real_cpu = tw_real_cpu_sum / tw_total
        effective_util_over_time = avg_real_cpu / capacity_total if capacity_total > 0 else 0.0
        imbalance_over_time = tw_cv_sum / tw_total
    else:
        avg_util_over_time = sum(util_samples) / len(util_samples) if util_samples else 0.0
        avg_cpu_util = sum(cpu_util_samples) / len(cpu_util_samples) if cpu_util_samples else 0.0
        avg_mem_util = sum(mem_util_samples) / len(mem_util_samples) if mem_util_samples else 0.0
        capacity_total = sum(m.cpu for m in machines)
        avg_real_cpu = sum(real_cpu_samples) / len(real_cpu_samples) if real_cpu_samples else 0.0
        effective_util_over_time = avg_real_cpu / capacity_total if capacity_total > 0 else 0.0
        imbalance_over_time = sum(cv_samples) / len(cv_samples) if cv_samples else 0.0
    
    # ⭐ 调试输出
    print(f"\n  [事件驱动统计]")
    print(f"    调度轮次: {num_scheduling_rounds}")
    print(f"    已调度: {scheduled_count}, 失败: {failed_count}")
    print(f"    采样次数: {len(util_samples)} (有任务运行时才采样)")
    print(f"    过程平均利用率(时间加权): {avg_util_over_time*100:.1f}%")
    print(f"    过程平均CPU利用率(时间加权): {avg_cpu_util*100:.1f}%")
    print(f"    过程平均真实利用率(时间加权): {effective_util_over_time*100:.1f}%")
    print(f"    过程不均衡CV(时间加权): {imbalance_over_time*100:.1f}%")
    print(f"    峰值利用率: {max_util_seen*100:.1f}%")
    print(f"    已释放任务: {scheduled_count - len(running_tasks)}")
    print(f"    仍在运行: {len(running_tasks)}")
    
    # ⭐ 警告：如果采样次数远小于调度轮次，说明大部分时间空闲
    if len(util_samples) < num_scheduling_rounds * 0.1:
        print(f"    ⚠️  警告: 采样次数({len(util_samples)})远小于调度轮次({num_scheduling_rounds})")
        print(f"           说明大部分时间集群空闲，可能需要减小调度间隔或增加任务并发")
    
    return {
        "scheduled": scheduled_count,
        "failed": failed_count,
        "machines": machines,
        "num_rounds": num_scheduling_rounds,
        "avg_util_over_time": avg_util_over_time,  # ⭐ 过程中的平均利用率（请求量）
        "max_util_seen": max_util_seen,            # ⭐ 过程中的峰值利用率
        "avg_cpu_util": avg_cpu_util,              # ⭐ 过程中的平均 CPU 利用率（请求量）
        "avg_mem_util": avg_mem_util,              # ⭐ 过程中的平均 MEM 利用率（请求量）
        "effective_util_over_time": effective_util_over_time,  # ⭐ 过程中的平均真实CPU利用率
        "imbalance_over_time": imbalance_over_time,            # ⭐ 过程中的平均不均衡CV
        "total_released": scheduled_count - len(running_tasks),  # 已释放任务数（修正计算）
        "all_scheduled_task_ids": all_scheduled_tasks,  # ⭐ 所有已调度任务ID
    }

