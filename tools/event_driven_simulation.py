#!/usr/bin/env python3
"""
严格按照 Firmament simulator.cc 的架构实现的事件驱动模拟器

参考源码:
- baselines/firmament/src/sim/simulator.cc::ReplaySimulation()
- baselines/firmament/src/sim/event_manager.cc
- baselines/firmament/src/sim/simulator_bridge.cc
"""
from __future__ import annotations
import heapq
from dataclasses import dataclass
from typing import List, Dict, Tuple, Callable, Any
from collections import defaultdict


@dataclass
class SimulationEvent:
    """模拟器事件（对应 EventDescriptor）"""
    timestamp: int  # 微秒
    event_type: str  # TASK_SUBMIT, TASK_END_RUNTIME, ADD_MACHINE, etc.
    data: Any


class EventManager:
    """事件管理器（对应 firmament/src/sim/event_manager.cc）"""
    def __init__(self):
        self.events = []  # 优先队列 (timestamp, event)
        self.num_events_processed = 0
    
    def add_event(self, timestamp: int, event_type: str, data: Any):
        """添加事件到队列"""
        heapq.heappush(self.events, (timestamp, SimulationEvent(timestamp, event_type, data)))
    
    def get_next_event(self) -> Tuple[int, SimulationEvent]:
        """获取下一个事件"""
        if not self.events:
            return (float('inf'), None)
        timestamp, event = heapq.heappop(self.events)
        self.num_events_processed += 1
        return (timestamp, event)
    
    def get_time_of_next_event(self) -> int:
        """获取下一个事件的时间"""
        if not self.events:
            return float('inf')
        return self.events[0][0]
    
    def has_simulation_completed(self, num_scheduling_rounds: int, 
                                 max_rounds: int, max_runtime: int) -> bool:
        """检查模拟是否完成"""
        if num_scheduling_rounds >= max_rounds:
            return True
        if self.get_time_of_next_event() >= max_runtime:
            return True
        return len(self.events) == 0


class SimulatorBridge:
    """
    模拟器桥接器（对应 simulator_bridge.cc）
    连接事件管理器和调度器
    """
    def __init__(self, event_manager: EventManager, scheduler, machines, task_dict: Dict):
        self.event_manager = event_manager
        self.scheduler = scheduler
        self.machines = machines
        self.task_dict = task_dict  # task_id -> Task object
        
        # 跟踪任务运行时长（对应 task_runtime_）
        self.task_runtime = {}  # task_id -> duration
        
        # 任务状态跟踪
        self.running_tasks = {}  # task_id -> machine_id
        self.scheduled_count = 0
        self.failed_count = 0
    
    def load_trace_data(self, tasks: List):
        """
        加载 trace 数据（对应 LoadTraceData）
        - 添加任务提交事件
        - 记录任务运行时长
        """
        for task in tasks:
            # 添加任务提交事件
            self.event_manager.add_event(
                task.arrival, 
                'TASK_SUBMIT', 
                task
            )
            # 记录任务运行时长（对应 LoadTasksRunningTime）
            if task.duration > 0:
                self.task_runtime[task.id] = task.duration
    
    def process_simulator_events(self, events_up_to_time: int):
        """
        处理所有 <= events_up_to_time 的事件
        对应 simulator_bridge.cc::ProcessSimulatorEvents()
        """
        while self.event_manager.get_time_of_next_event() <= events_up_to_time:
            timestamp, event = self.event_manager.get_next_event()
            
            if event is None:
                break
            
            if event.event_type == 'TASK_END_RUNTIME':
                # ⭐ 任务完成 -> 释放资源
                self.task_completed(event.data)
            
            elif event.event_type == 'TASK_SUBMIT':
                # 任务提交暂存，等待调度器运行
                pass
    
    def task_completed(self, task_id: str):
        """
        任务完成处理（对应 TaskCompleted）
        调用调度器的 HandleTaskCompletion -> 资源释放
        """
        if task_id not in self.running_tasks:
            return
        
        machine_id = self.running_tasks.pop(task_id)
        task = self.task_dict.get(task_id)
        
        if task:
            # ⭐ 释放资源（对应 UnbindTaskFromResource）
            self.machines[machine_id].cpu_used -= task.cpu
            self.machines[machine_id].mem_used -= task.mem
            self.machines[machine_id].cpu_used = max(0, self.machines[machine_id].cpu_used)
            self.machines[machine_id].mem_used = max(0, self.machines[machine_id].mem_used)
    
    def on_task_placement(self, task, machine_id: int, current_time: int):
        """
        任务放置处理（对应 OnTaskPlacement）
        添加任务结束事件
        """
        task_id = task.id
        self.running_tasks[task_id] = machine_id
        self.scheduled_count += 1
        
        # 占用资源
        self.machines[machine_id].cpu_used += task.cpu
        self.machines[machine_id].mem_used += task.mem
        self.machines[machine_id].tasks.append((task_id, task.tenant))
        
        # ⭐ 添加任务结束事件（如果有运行时长）
        if task_id in self.task_runtime:
            end_time = current_time + self.task_runtime[task_id]
            self.event_manager.add_event(end_time, 'TASK_END_RUNTIME', task_id)
    
    def schedule_jobs(self, pending_tasks: List, current_time: int):
        """
        运行调度器（对应 ScheduleJobs）
        """
        if not pending_tasks:
            return
        
        # 调用具体的调度算法
        placements = self.scheduler.schedule(pending_tasks, self.machines)
        
        # 处理调度结果
        for task_id, machine_id in placements:
            task = self.task_dict.get(task_id)
            if task:
                self.on_task_placement(task, machine_id, current_time)
        
        # 记录失败的任务
        scheduled_ids = {tid for tid, _ in placements}
        for task in pending_tasks:
            if task.id not in scheduled_ids:
                self.failed_count += 1


def run_event_driven_simulation(
    scheduler,
    tasks: List,
    machines: List,
    batch_step: int = 1000000,  # 1秒（微秒）
    max_scheduling_rounds: int = 100000,
    max_runtime: int = float('inf')
) -> Dict:
    """
    严格按照 Firmament simulator.cc::ReplaySimulation() 的架构
    
    Args:
        scheduler: 调度器对象（需要实现 schedule(tasks, machines) 方法）
        tasks: 任务列表
        machines: 机器列表
        batch_step: 批量模式间隔（微秒），对应 FLAGS_batch_step
        max_scheduling_rounds: 最大调度轮数，对应 FLAGS_max_scheduling_rounds
        max_runtime: 最大运行时间（微秒），对应 FLAGS_runtime
    
    Returns:
        调度结果字典
    """
    # 1. 初始化事件管理器
    event_manager = EventManager()
    
    # 2. 初始化模拟器桥接器
    task_dict = {t.id: t for t in tasks}
    bridge = SimulatorBridge(event_manager, scheduler, machines, task_dict)
    
    # 3. 加载 trace 数据（添加所有任务提交事件）
    bridge.load_trace_data(tasks)
    
    # 4. 主模拟循环（对应 ReplaySimulation 的 while 循环）
    run_scheduler_at = 0
    num_scheduling_rounds = 0
    pending_tasks = []
    
    while not event_manager.has_simulation_completed(
        num_scheduling_rounds, max_scheduling_rounds, max_runtime
    ):
        # ⭐ 4.1 处理所有 <= run_scheduler_at 的事件
        # 包括 TASK_END_RUNTIME（资源释放）
        bridge.process_simulator_events(run_scheduler_at)
        
        # ⭐ 4.2 收集到达的任务（在 run_scheduler_at 时刻）
        while event_manager.get_time_of_next_event() == run_scheduler_at:
            timestamp, event = event_manager.get_next_event()
            if event and event.event_type == 'TASK_SUBMIT':
                pending_tasks.append(event.data)
        
        # ⭐ 4.3 运行调度器（对应 ScheduleJobsHelper）
        if pending_tasks:
            bridge.schedule_jobs(pending_tasks, run_scheduler_at)
            pending_tasks = []
        
        # ⭐ 4.4 推进到下一个调度时间（批量模式：固定间隔）
        run_scheduler_at += batch_step
        num_scheduling_rounds += 1
    
    # 5. 处理剩余事件
    bridge.process_simulator_events(max_runtime)
    
    return {
        "scheduled": bridge.scheduled_count,
        "failed": bridge.failed_count,
        "machines": machines,
        "num_rounds": num_scheduling_rounds,
    }


# ==================== 调度器适配器 ====================

class FirmamentSchedulerAdapter:
    """Firmament 调度器适配器"""
    def __init__(self, original_scheduler):
        self.original_scheduler = original_scheduler
    
    def schedule(self, tasks, machines):
        """调用原始调度器并返回 placements"""
        return self.original_scheduler.schedule(tasks)


class MesosDRFSchedulerAdapter:
    """Mesos DRF 调度器适配器"""
    def __init__(self, allocator):
        self.allocator = allocator
    
    def schedule(self, tasks, machines):
        """调用 Mesos allocator"""
        from collections import defaultdict
        tasks_by_fw = defaultdict(list)
        for task in tasks:
            tasks_by_fw[task.tenant].append(task)
        return self.allocator.allocate(tasks_by_fw)


class TetrisSchedulerAdapter:
    """Tetris 调度器适配器"""
    def __init__(self):
        pass
    
    def schedule(self, tasks, machines):
        """Tetris 贪心调度"""
        placements = []
        for task in tasks:
            best_score = -float('inf')
            best_machine = None
            
            for machine in machines:
                if machine.cpu - machine.cpu_used >= task.cpu and \
                   machine.mem - machine.mem_used >= task.mem:
                    # Tetris 评分函数
                    after_cpu = (machine.cpu_used + task.cpu) / machine.cpu
                    after_mem = (machine.mem_used + task.mem) / machine.mem
                    score = after_cpu * after_mem
                    
                    if score > best_score:
                        best_score = score
                        best_machine = machine
            
            if best_machine:
                placements.append((task.id, best_machine.id))
        
        return placements

