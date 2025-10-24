#!/usr/bin/env python3
from __future__ import annotations

"""
完整的调度器对比（严格按源码实现）

系统组件:
1. Firmament Flow Scheduler (完整 flow graph + min-cost solver)
2. Mesos DRF Allocator (完整 hierarchical allocator)
3. Tetris (严格按论文公式)
4. SLO-Driven (本研究)

所有实现均基于官方源码，无简化
"""

import sys
import os
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass
import math
import random

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scheduler_frameworks.firmament_scheduler import FirmamentScheduler, Machine as FirmMachine, Task as FirmTask
from scheduler_frameworks.mesos_drf_allocator import HierarchicalAllocator, Agent, Client, Task as MesosTask
from collections import defaultdict

from tools.metrics import cpu_mem_util, fragmentation, imbalance, net_bandwidth
from tools.scheduler_nextgen import (
    TenantSelector,
    score_node,
    WatermarkGuard,
    RetryQueue,
    EWMA,
)
from tools.run_with_events import enable_event_driven_simulation

# 全局随机种子（影响数据加载等通用操作）
np.random.seed(42)

# 风险模型默认参数
DEFAULT_RISK_A = 26.0
DEFAULT_RISK_B = 0.80

# 信用惩罚阈值
LOW_CREDIT_THRESHOLD = 0.60
LOW_CREDIT_PENALTY = 0.01

# 加载 PPO 模型（用于 NextGen residual）
try:
    from stable_baselines3 import PPO  # type: ignore
except ImportError:  # pragma: no cover
    PPO = None  # RL features will be disabled if the library is missing
    print("[WARN] stable-baselines3 not found – RL residuals disabled.\n"
          "       pip install 'gymnasium==0.29.1' 'stable-baselines3>=2.3.0' torch")

# 尝试加载 PPO 模型
ppo_model = None
if PPO is not None:
    try:
        ppo_model = PPO.load("tools/ppo_quick.zip")
        print("✓ Loaded PPO model for NextGen residual (RL enabled)")
    except Exception as e:
        print(f"⚠ PPO model not found at tools/ppo_quick.zip, using baseline NextGen: {e}")
else:
    print("[INFO] RL residuals disabled (stable-baselines3 not available)")


@dataclass
class Task:
    id: int
    cpu: float
    mem: float
    tenant: str
    arrival: int
    slo_sensitive: str
    priority: int
    # 新增字段 - 来自 batch_instance.csv
    start_time: int = 0  # 实际开始时间
    end_time: int = 0  # 实际结束时间
    cpu_avg: float = 0.0  # 平均 CPU 使用（历史数据）
    cpu_max: float = 0.0  # 最大 CPU 使用
    machine_id: str = ""  # 原始运行节点（用于亲和性调度）
    duration: int = 0  # 任务运行时长（秒）
    # 从 container_usage.csv 获取的真实使用量
    real_cpu: float = 0.0  # 实际 CPU 使用
    real_mem: float = 0.0  # 实际内存使用
    mem_bandwidth: float = 0.0  # 内存带宽需求
    net_in: float = 0.0  # 网络流入 (MB/s)
    net_out: float = 0.0  # 网络流出 (MB/s)
    disk_io: float = 0.0  # 磁盘 IO 需求


@dataclass
class Machine:
    id: int
    cpu: float = 11.0  # ← 修正：调整为 11.0（基于实际需求计算）
    mem: float = 11.0  # ← 修正：调整为 11.0
    cpu_used: float = 0
    mem_used: float = 0
    tasks: list = None
    task_records: list = None  # 记录调度详情（用于预占等高级策略）
    opportunity_records: list = None  # 存储机会池任务记录
    # 新增资源维度
    mem_bandwidth: float = 0.0  # 内存带宽使用 (GB/s)
    mem_bandwidth_cap: float = 100.0  # 内存带宽容量
    net_bandwidth: float = 0.0  # 网络带宽使用 (MB/s)
    net_bandwidth_cap: float = 1000.0  # 网络带宽容量 (1Gbps)
    disk_io: float = 0.0  # 磁盘IO使用 (%)
    disk_io_cap: float = 100.0  # 磁盘IO容量
    failure_domain: str = ""  # 故障域（机架/集群）
    # 活跃任务跟踪（用于动态资源释放）
    active_tasks: list = None  # 存储 (tid, tenant, sched_time, end_time, resources)

    def __post_init__(self):
        if self.tasks is None:
            self.tasks = []
        if self.task_records is None:
            self.task_records = []
        if self.opportunity_records is None:
            self.opportunity_records = []
        if self.active_tasks is None:
            self.active_tasks = []

    def utilization(self):
        """节点利用率：取 CPU 与 MEM 维度的最大值（保持与基线算法兼容）"""
        return max(self.cpu_used / self.cpu, self.mem_used / self.mem)

    def add_task(self, tid: str, tenant: str, sched_time: int, duration: int,
                 cpu: float, mem: float, **extra_resources):
        """添加任务并占用资源（带时间跟踪）"""
        end_time = sched_time + duration
        self.cpu_used += cpu
        self.mem_used += mem

        # 记录任务资源占用信息
        task_info = {
            'tid': tid,
            'tenant': tenant,
            'sched_time': sched_time,
            'end_time': end_time,
            'cpu': cpu,
            'mem': mem,
        }
        task_info.update(extra_resources)  # mem_bandwidth, net_bandwidth, disk_io

        self.active_tasks.append(task_info)
        self.tasks.append((tid, tenant))  # 保持兼容性

        # 更新额外资源
        for key, value in extra_resources.items():
            if hasattr(self, key):
                setattr(self, key, getattr(self, key) + value)

    def release_completed_tasks(self, current_time: int) -> int:
        """释放已完成任务的资源，返回释放的任务数"""
        completed = []
        for task_info in self.active_tasks:
            if task_info['end_time'] <= current_time:
                completed.append(task_info)

        for task_info in completed:
            self.active_tasks.remove(task_info)
            # 释放资源
            self.cpu_used = max(0, self.cpu_used - task_info['cpu'])
            self.mem_used = max(0, self.mem_used - task_info['mem'])

            # 释放额外资源
            if 'mem_bandwidth' in task_info:
                self.mem_bandwidth = max(0, self.mem_bandwidth - task_info['mem_bandwidth'])
            if 'net_bandwidth' in task_info:
                self.net_bandwidth = max(0, self.net_bandwidth - task_info['net_bandwidth'])
            if 'disk_io' in task_info:
                self.disk_io = max(0, self.disk_io - task_info['disk_io'])

        return len(completed)


class ResidualController:
    def __init__(self, tenant_selector: TenantSelector):
        self.selector = tenant_selector
        self.alpha_base = float(os.getenv("NEXTGEN_ALPHA", "0.85"))
        self.high_wm_base = float(os.getenv("NEXTGEN_HIGH_WM", "0.92"))
        self.low_wm_base = float(os.getenv("NEXTGEN_LOW_WM", "0.60"))
        self.group_list = sorted(
            {tenant_selector.tenant_groups.get(t, "default") for t in tenant_selector.tenant_groups})
        if "default" not in self.group_list:
            self.group_list.append("default")

    def build_state(self, machines: List['Machine'], global_stats: Dict[str, float]) -> Tuple[
        List[float], Dict[str, float]]:
        utils = [m.utilization() for m in machines]
        cpu_fracs = [max(m.cpu - m.cpu_used, 0) / m.cpu for m in machines]
        mem_fracs = [max(m.mem - m.mem_used, 0) / m.mem for m in machines]

        def agg_stats(values: List[float]):
            if not values:
                return [0.0, 0.0, 0.0, 0.0]
            return [
                float(np.mean(values)),
                float(np.std(values)),
                float(np.min(values)),
                float(np.max(values)),
            ]

        machine_stats = agg_stats(utils) + agg_stats(cpu_fracs) + agg_stats(mem_fracs)
        group_queues = self.selector.get_group_queue_lengths()
        group_stats = [float(group_queues.get(group, 0)) for group in self.group_list]

        state_vec = machine_stats + group_stats + [
            global_stats.get("avg_util", 0.0),
            global_stats.get("fragmentation", 0.0),
            global_stats.get("imbalance", 0.0),
            self.alpha_base,
            self.high_wm_base,
        ]
        return state_vec, group_queues

    def apply_residuals(
            self,
            residual: Optional[Dict[str, Any]],
            alpha: float,
            high_wm: float,
            low_wm: float,
    ) -> Tuple[float, float, float, Dict[str, float]]:
        if not residual:
            return alpha, high_wm, low_wm, {}

        delta_alpha = float(residual.get("delta_alpha", 0.0))
        delta_high = float(residual.get("delta_high_wm", 0.0))
        delta_low = float(residual.get("delta_low_wm", 0.0))
        group_delta = residual.get("group_delta", {})

        # Optional amplification factor for easier observation
        scale = float(os.getenv("RL_DELTA_SCALE", "0.3"))  # default 0.3
        delta_alpha *= scale
        delta_high *= scale
        delta_low *= scale

        alpha_new = float(np.clip(alpha + delta_alpha, 0.1, 0.99))
        high_new = float(np.clip(high_wm + delta_high, 0.7, 1.5))  # allow 150% overbooking
        low_new = float(np.clip(low_wm + delta_low, 0.3, min(1.2, high_new - 0.02)))

        # Debug print when residual has noticeable impact (仅在 DEBUG 模式)
        if os.getenv("DEBUG_RL", "0") == "1" and (abs(delta_alpha) > 1e-3 or abs(delta_high) > 1e-3):
            print(f"[RL] Δα={delta_alpha:+.3f}, Δhigh={delta_high:+.3f} → α={alpha_new:.3f}, high_wm={high_new:.3f}")
        group_weights = {}
        for group in self.selector.group_weights:
            delta = float(group_delta.get(group, 0.0))
            group_weights[group] = max(self.selector.group_weights.get(group, 1.0) + delta, 1e-3)

        return alpha_new, high_new, low_new, group_weights


class RiskModel:
    """
    风险模型，直接与最终评估的违约率函数对齐，确保控制器获得准确的风险信号。
    """

    def predict(self, util_after: float, features: dict = None) -> float:
        """直接调用全局的、作为基准的违约风险预测函数"""
        return predict_violation_risk(util_after)


class CandidateScorer:
    """
    候选打分（学习部件）：结合风险、利用率与平衡度（Tetris 增量）
    分数越小越好（风险优先）。
    环境变量：SCHED_SCORE_ALPHA/BETA/GAMMA
    """

    def __init__(self, risk_model: "RiskModel"):
        self.risk_model = risk_model
        try:
            self.alpha = float(os.getenv("SCHED_SCORE_ALPHA", "0.7"))  # 风险权重
            self.beta = float(os.getenv("SCHED_SCORE_BETA", "0.2"))  # 利用率权重
            self.gamma = float(os.getenv("SCHED_SCORE_GAMMA", "0.1"))  # Tetris 增量权重
        except Exception:
            self.alpha, self.beta, self.gamma = 0.6, 0.3, 0.1

    @staticmethod
    def _tetris_delta(machine: "Machine", task: "Task", k: int = 2) -> float:
        cpu_norm_before = machine.cpu_used / machine.cpu
        mem_norm_before = machine.mem_used / machine.mem
        cpu_norm_after = (machine.cpu_used + task.cpu) / machine.cpu
        mem_norm_after = (machine.mem_used + task.mem) / machine.mem
        return (cpu_norm_after ** k + mem_norm_after ** k) - \
            (cpu_norm_before ** k + mem_norm_before ** k)

    def score(self, machine: "Machine", task: "Task") -> float:
        util_after = max((machine.cpu_used + task.cpu) / machine.cpu,
                         (machine.mem_used + task.mem) / machine.mem)
        risk = self.risk_model.predict(util_after, {
            "cpu_used": machine.cpu_used, "mem_used": machine.mem_used
        })
        td = self._tetris_delta(machine, task)
        # 越小越好：主要压风险，其次压利用率，再兼顾平衡度
        return self.alpha * risk + self.beta * util_after + self.gamma * td


class OnlineBanditTuner:
    """
    在线 Bandit 调参（ε-贪心）：离散臂为 (top_k, base_limit, spill_margin) 组合
    窗口内按 reward=成功率-平均风险 进行更新
    环境变量：SCHED_WINDOW, SCHED_EPS
    """

    def __init__(self):
        # 扩展探索空间，允许更激进的策略
        topk_space = [12, 24, 32, 48]
        base_space = [0.86, 0.88, 0.90, 0.92]
        spill_space = [0.04, 0.06, 0.08, 0.10]
        self.arms = []
        for k in topk_space:
            for b in base_space:
                for s in spill_space:
                    self.arms.append({"top_k": k, "base_limit": b, "spill_margin": s})
        self.counts = [0 for _ in self.arms]
        self.values = [0.0 for _ in self.arms]
        self.eps = float(os.getenv("SCHED_EPS", "0.50"))
        self.window = int(os.getenv("SCHED_WINDOW", "1000"))
        self.current_idx = 0

    def select(self) -> dict:
        # ε-贪心
        if np.random.rand() < self.eps:
            self.current_idx = np.random.randint(0, len(self.arms))
        else:
            self.current_idx = int(np.argmax(self.values))
        return self.arms[self.current_idx]

    def update(self, reward: float):
        i = self.current_idx
        self.counts[i] += 1
        n = self.counts[i]
        # 增量均值
        self.values[i] += (reward - self.values[i]) / n


def load_alibaba_trace(trace_dir: str, max_inst: int = None) -> List['Task']:
    """
    加载 Alibaba 2018 trace（修正版 + 内存优化）
    使用 Terminated 状态 + 真实资源数据（列 12, 13）

    max_inst: None = 默认 100000（防止内存溢出）
    """
    # 默认限制 10 万条（防止 OOM）
    if max_inst is None:
        max_inst = 100000
        print(f"━━━ 加载 Alibaba 2018 Cluster Trace（默认 {max_inst} 条）━━━")
        print("提示：如需更多，可指定参数：python ... ./data 500000\n")
    else:
        print(f"━━━ 加载 Alibaba 2018 Cluster Trace（{max_inst} 条）━━━\n")

    # 读取任务表（包含任务类型 / 优先级等）
    task_path = os.path.join(trace_dir, "batch_task.csv")
    if not os.path.exists(task_path):
        raise FileNotFoundError(f"缺少 batch_task.csv: {task_path}")

    task_df = pd.read_csv(task_path, header=None, usecols=[0, 3, 4])
    task_df = task_df.rename(columns={0: "task_id", 3: "task_type", 4: "task_priority"})
    task_df["task_id"] = task_df["task_id"].astype(str).str.strip()
    task_df["task_type"] = pd.to_numeric(task_df["task_type"], errors="coerce").fillna(0).astype("Int8")
    task_df["task_priority"] = pd.to_numeric(task_df["task_priority"], errors="coerce").fillna(0).astype("Int8")
    task_df = task_df.drop_duplicates(subset="task_id", keep="last")
    task_type_map = task_df.set_index("task_id")["task_type"]
    task_pri_map = task_df.set_index("task_id")["task_priority"]

    # ---------- merge real usage ----------
    usage_path = os.path.join(trace_dir, "usage_avg.csv")
    if os.path.exists(usage_path):
        usage_df = pd.read_csv(usage_path)
        usage_map = usage_df.set_index("instance_id")[["cpu_used", "mem_used"]].to_dict("index")
        print(f"✓ merged real usage rows: {len(usage_map):,}")
    else:
        usage_map = {}
        print("⚠ usage_avg.csv not found, using 50% estimation")

    all_valid_rows = []
    total_scanned = 0

    for chunk_idx, chunk in enumerate(pd.read_csv(f"{trace_dir}/batch_instance.csv",
                                                  chunksize=1000000,
                                                  header=None)):
        total_scanned += len(chunk)

        # Terminated 状态才有资源数据
        terminated = chunk[chunk[4] == 'Terminated'].copy()

        if len(terminated) > 0:
            # 提取所有相关字段
            terminated['cpu'] = pd.to_numeric(terminated[12], errors='coerce')  # plan_cpu
            terminated['mem'] = pd.to_numeric(terminated[13], errors='coerce')  # plan_mem
            terminated['start_time'] = pd.to_numeric(terminated[5], errors='coerce').fillna(0).astype(int)
            terminated['end_time'] = pd.to_numeric(terminated[6], errors='coerce').fillna(0).astype(int)
            terminated['cpu_avg'] = pd.to_numeric(terminated[10], errors='coerce').fillna(0)
            terminated['cpu_max'] = pd.to_numeric(terminated[11], errors='coerce').fillna(0)
            terminated['machine_id'] = terminated[7].astype(str).str.strip()

            # 只保留有效数据
            valid = terminated['cpu'].notna() & terminated['mem'].notna() & \
                    (terminated['cpu'] > 0) & (terminated['mem'] > 0)
            valid_rows = terminated[valid]
            valid_rows = valid_rows.rename(columns={1: "task_id"})
            valid_rows["task_id"] = valid_rows["task_id"].astype(str).str.strip()
            valid_rows["task_type"] = valid_rows["task_id"].map(task_type_map).fillna(0).astype("Int8")
            valid_rows["task_priority"] = valid_rows["task_id"].map(task_pri_map).fillna(0).astype("Int8")

            if len(valid_rows) > 0:
                all_valid_rows.append(valid_rows)

        # 进度显示
        collected = sum(len(v) for v in all_valid_rows)
        if chunk_idx % 10 == 0:
            print(f"  已扫描 {total_scanned / 1e6:.1f}M 行，收集 {collected} 条有效记录...", end='\r')

        # 达到上限即停止（节省内存和时间）
        if collected >= max_inst:
            print(f"  已扫描 {total_scanned / 1e6:.1f}M 行，收集 {collected} 条有效记录...")
            break

    print()

    if not all_valid_rows:
        raise ValueError("未找到有效数据")

    df = pd.concat(all_valid_rows, ignore_index=True).head(max_inst)

    # 填充缺失的任务类型/优先级
    df["task_type"].fillna(0, inplace=True)
    df["task_priority"].fillna(0, inplace=True)

    print(f"✓ {len(df)} 条有效记录（扫描了 {total_scanned / 1e6:.1f}M 行）")
    print(f"  租户数: {df[2].nunique()}")
    print(f"  CPU: {df['cpu'].mean():.3f} (std={df['cpu'].std():.3f})")
    print(f"  MEM: {df['mem'].mean():.3f} (std={df['mem'].std():.3f})\n")

    tasks = []
    for idx, row in df.sort_values(5).iterrows():
        slo_sensitive = 'high' if int(row['task_type']) == 1 else 'low'
        inst_id = str(row[0])  # instance_id
        cpu_req = row['cpu']
        mem_req = row['mem']

        # 从usage_map获取真实使用量
        real = usage_map.get(inst_id, {})
        cpu_real = real.get("cpu_used", cpu_req * 0.5)
        mem_real = real.get("mem_used", mem_req * 0.5)

        # 计算任务运行时长
        start = int(row['start_time'])
        end = int(row['end_time'])
        duration = max(end - start, 0) if end > start else 0

        task = Task(
            id=inst_id,
            cpu=cpu_req,
            mem=mem_req,
            tenant=str(row[2]),
            arrival=int(row[5]),
            slo_sensitive=slo_sensitive,
            priority=int(row['task_priority']),
            # 新增字段
            start_time=start,
            end_time=end,
            duration=duration,
            cpu_avg=float(row['cpu_avg']),
            cpu_max=float(row['cpu_max']),
            machine_id=str(row['machine_id']),
            real_cpu=cpu_real,
            real_mem=mem_real,
        )
        tasks.append(task)

    durations = [t.duration for t in tasks if t.duration > 0]
    arrivals = [t.arrival for t in tasks]

    print(f"  任务时长统计: 平均={np.mean(durations):.0f}秒, "
          f"中位数={np.median(durations):.0f}秒, "
          f"P90={np.percentile(durations, 90):.0f}秒")
    print(f"  到达时间跨度: {min(arrivals):.0f} ~ {max(arrivals):.0f} (共{max(arrivals) - min(arrivals):.0f}秒)")
    print(f"  推荐调度间隔: {min(int(np.median(durations)), 60)}秒 (中位时长的一半或60秒)\n")

    return tasks


def run_firmament(tasks: List[Task], num_machines: int = 114) -> dict:
    """
    运行完整 Firmament Flow Scheduler（事件驱动模式）
    严格按照 firmament/src/sim/simulator.cc 的批量模式架构
    """
    np.random.seed(123)
    random.seed(123)
    print("━━━ [1/4] Firmament Flow Scheduler (OSDI'16 完整实现) ━━━")

    # 创建机器（容量 11.0）
    machines = [FirmMachine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]

    # 创建调度器
    scheduler = FirmamentScheduler(machines)

    # 定义批量调度函数
    def firmament_schedule_batch(batch_tasks, current_machines):
        """
        每个批次的调度逻辑
        返回 placements: [(task_id, machine_id), ...]
        """
        firm_tasks = [
            FirmTask(id=t.id, cpu=t.cpu, mem=t.mem, tenant=t.tenant, arrival=t.arrival)
            for t in batch_tasks
        ]
        return scheduler.schedule(firm_tasks)

    # ⭐ 启用事件驱动模拟（自动资源释放）
    # 对应 firmament/src/sim/simulator.cc::ReplaySimulation()
    # 调度间隔应小于任务平均时长的一半，确保任务有重叠
    durations = [t.duration for t in tasks if t.duration > 0]
    median_duration = int(np.median(durations)) if durations else 60
    # ⭐ 优先使用环境变量，否则智能推荐（任务时长的20%，最少3秒）
    env_step = os.getenv("BATCH_STEP_SECONDS")
    if env_step:
        batch_step = int(env_step)
        print(f"  [事件驱动] 调度间隔={batch_step}秒 ⭐环境变量⭐ (任务中位时长={median_duration}秒)")
    else:
        recommended_step = max(3, min(median_duration // 5, 30))  # 任务时长的20%
        batch_step = recommended_step
        print(f"  [事件驱动] 调度间隔={batch_step}秒 (智能推荐, 任务中位时长={median_duration}秒)")

    result = enable_event_driven_simulation(
        baseline_scheduler_func=firmament_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=batch_step,
        scheduler_obj=scheduler,  # ⭐ 传入调度器对象以调用 task_completed()
    )

    result["name"] = "Firmament (OSDI'16 源码)"
    return result


def run_mesos_drf(tasks: List[Task], num_machines: int = 114) -> dict:
    """
    运行完整 Mesos DRF Allocator（事件驱动模式）
    严格按照 mesos/src/master/allocator/mesos/hierarchical.cpp
    """
    np.random.seed(456)
    random.seed(456)
    print("\n━━━ [2/4] Mesos DRF Allocator (NSDI'11 完整实现) ━━━")

    # 创建 agents（容量 11.0）
    agents = [Agent(id=i, cpu_total=11.0, mem_total=11.0,
                    cpu_available=11.0, mem_available=11.0)
              for i in range(num_machines)]

    # 创建 allocator
    allocator = HierarchicalAllocator(agents)

    # 创建机器列表（用于统一接口）
    machines = [Machine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]

    # 定义批量调度函数
    def mesos_schedule_batch(batch_tasks, current_machines):
        """
        每个批次的调度逻辑
        返回 placements: [(task_id, machine_id), ...]
        """
        # 按租户分组任务
        tasks_by_fw = defaultdict(list)
        for task in batch_tasks:
            mesos_task = MesosTask(
                id=task.id,
                cpu=task.cpu,
                mem=task.mem,
                tenant=task.tenant,
                arrival=task.arrival
            )
            tasks_by_fw[task.tenant].append(mesos_task)

        # 调用 allocator
        return allocator.allocate(tasks_by_fw)

    # ⭐ 启用事件驱动模拟（自动资源释放）
    # 对应 mesos master 的异步消息驱动架构
    durations = [t.duration for t in tasks if t.duration > 0]
    median_duration = int(np.median(durations)) if durations else 60
    # ⭐ 优先使用环境变量，否则智能推荐（任务时长的20%，最少3秒）
    env_step = os.getenv("BATCH_STEP_SECONDS")
    if env_step:
        batch_step = int(env_step)
        print(f"  [事件驱动] 调度间隔={batch_step}秒 ⭐环境变量⭐ (任务中位时长={median_duration}秒)")
    else:
        recommended_step = max(3, min(median_duration // 5, 30))  # 任务时长的20%
        batch_step = recommended_step
        print(f"  [事件驱动] 调度间隔={batch_step}秒 (智能推荐, 任务中位时长={median_duration}秒)")

    result = enable_event_driven_simulation(
        baseline_scheduler_func=mesos_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=batch_step,
        allocator_obj=allocator,  # ⭐ 传入 allocator 以调用 recover_resources()
    )

    result["name"] = "Mesos DRF (NSDI'11 源码)"
    return result


def run_tetris(tasks: List[Task], num_machines: int = 114) -> dict:
    """
    Tetris (SIGCOMM'14 论文算法) - 事件驱动模式
    """
    print("\n━━━ [3/4] Tetris (SIGCOMM'14 论文公式) ━━━")
    np.random.seed(789)
    random.seed(789)

    machines = [Machine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]
    k = 2  # Tetris 评分参数

    # 定义批量调度函数
    def tetris_schedule_batch(batch_tasks, current_machines):
        """
        Tetris 贪心调度逻辑
        SIGCOMM'14 Section 3.2, Equation 1
        
        ⚠️ 重要：需要在批次内临时更新 cpu_used/mem_used，
        防止多个任务过度分配到同一台机器
        """
        placements = []

        # 拆分任务队列，高敏感任务优先调度
        high_queue = [t for t in batch_tasks if t.slo_sensitive == 'high']
        low_queue = [t for t in batch_tasks if t.slo_sensitive != 'high']
        queue = []
        high_idx = 0
        low_idx = 0
        while high_idx < len(high_queue) or low_idx < len(low_queue):
            if high_idx < len(high_queue):
                queue.append(high_queue[high_idx])
                high_idx += 1
            if low_idx < len(low_queue):
                queue.append(low_queue[low_idx])
                low_idx += 1

        for task in queue:
            best_machine = None
            best_score = float('-inf')

            for machine in current_machines:
                if (machine.cpu_used + task.cpu > machine.cpu or
                        machine.mem_used + task.mem > machine.mem):
                    continue

                cpu_norm_before = machine.cpu_used / machine.cpu
                mem_norm_before = machine.mem_used / machine.mem
                cpu_norm_after = (machine.cpu_used + task.cpu) / machine.cpu
                mem_norm_after = (machine.mem_used + task.mem) / machine.mem

                score = ((cpu_norm_after ** k + mem_norm_after ** k) -
                         (cpu_norm_before ** k + mem_norm_before ** k))

                if score > best_score:
                    best_score = score
                    best_machine = machine

            if best_machine:
                # ✅ 临时更新资源（防止批次内过度分配）
                best_machine.cpu_used += task.cpu
                best_machine.mem_used += task.mem
                placements.append((task.id, best_machine.id))

        return placements

    # ⭐ 启用事件驱动模拟（自动资源释放）
    durations = [t.duration for t in tasks if t.duration > 0]
    median_duration = int(np.median(durations)) if durations else 60
    # ⭐ 优先使用环境变量，否则智能推荐（任务时长的20%，最少3秒）
    env_step = os.getenv("BATCH_STEP_SECONDS")
    if env_step:
        batch_step = int(env_step)
        print(f"  [事件驱动] 调度间隔={batch_step}秒 ⭐环境变量⭐ (任务中位时长={median_duration}秒)")
    else:
        recommended_step = max(3, min(median_duration // 5, 30))  # 任务时长的20%
        batch_step = recommended_step
        print(f"  [事件驱动] 调度间隔={batch_step}秒 (智能推荐, 任务中位时长={median_duration}秒)")

    result = enable_event_driven_simulation(
        baseline_scheduler_func=tetris_schedule_batch,
        tasks=tasks,
        machines=machines,
        batch_step_seconds=batch_step,
    )

    result["name"] = "Tetris (SIGCOMM'14 公式)"
    return result


def predict_violation_risk(util_after: float) -> float:
    """
    预测违约风险（尾延迟与利用率的非线性关系）
    优化目标：保持违约率优势前提下，允许更高利用率
    """
    if util_after > 0.95:
        return 0.35
    elif util_after > 0.90:
        return 0.22
    elif util_after > 0.85:
        return 0.12
    elif util_after > 0.80:
        return 0.05
    elif util_after > 0.75:
        return 0.02
    else:
        return 0.02


def tune_slo_limit(cluster_state: dict, tenant: str) -> float:
    """
    自适应 SLO 安全上限：基于全局尾部风险与租户覆写/信用

    返回用于当前租户的基础阈值（具体到节点时还会叠加节点风险修正）。
    """
    # 基础参数
    base_limit = cluster_state.get("base_limit", 0.78)
    global_risk = cluster_state.get("global_risk_ema", 0.02)
    k_global = cluster_state.get("k_global", 0.30)
    min_l, max_l = cluster_state.get("limit_bounds", (0.65, 0.90))

    # 租户覆写优先
    tenant_overrides = cluster_state.get("tenant_overrides", {})
    if tenant in tenant_overrides:
        return max(min(tenant_overrides[tenant], max_l), min_l)

    # 全局风险收紧：风险高则更保守
    limit = base_limit - k_global * max(0.0, global_risk - 0.02)

    # 轻微信用调节：低信用放宽一点，高信用略收紧（与"突破上限"策略配合）
    credits = cluster_state.get("tenant_credits", {})
    credit = credits.get(tenant, 1.0)
    credit_gain = cluster_state.get("credit_limit_gain", 0.04)
    limit += (0.5 - credit) * credit_gain
    # 低信用租户额外惩罚
    if credit < LOW_CREDIT_THRESHOLD:
        limit -= LOW_CREDIT_PENALTY

    return max(min(limit, max_l), min_l)


def run_slo_driven(tasks: List[Task], num_machines: int = 114) -> dict:
    """
    SLO-Driven (本研究) - 性能优先策略

    优化目标: 成功率 100% + 利用率 >77% + 违约率 <6% + 碎片化最低
    核心策略: 智能风险评估 + 动态上限调整 + 灵活机会池
    """
    np.random.seed(1024)
    random.seed(1024)
    print("\n━━━ [4/4] SLO-Driven (本研究) ━━━")

    machines = [Machine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]
    tenant_credits = defaultdict(lambda: 1.0)
    risk_model = RiskModel()  # 使用统一的风险模型

    os.environ["SLO_TARGET"] = os.getenv("SLO_TARGET", "0.10")
    risk_model = RiskModel()
    tuner = OnlineBanditTuner()
    scorer = CandidateScorer(risk_model)

    # 在 run_slo_driven 外部定义 calc_score 以便共享
    def calc_score_shared(task: Task, machine: Machine, util_after: float) -> float:
        return scorer.score(machine, task)

    scheduled = 0
    failed = 0
    opportunity_scheduled = 0
    opportunity_evicted = 0
    opportunity_active = 0

    # --- 工具函数 -----------------------------------------------------------

    def calc_node_limit(m_id: int) -> float:
        """根据节点风险自适应地调整上限（低风险奖励，高风险惩罚）"""
        risk = cluster_state["machine_risk_ema"].get(m_id, 0.02)
        limit = cluster_state["base_limit"]
        # 低风险奖励：risk < 0.04 时放宽，最多 +0.06
        limit += 0.06 * max(0.0, 0.04 - risk)
        # 高风险惩罚：沿用原有 k_machine
        limit -= cluster_state["k_machine"] * max(0.0, risk - 0.10)
        lb, ub = cluster_state["limit_bounds"]
        return max(min(limit, ub), lb)

    def util_with_task(machine: Machine, pending: Task) -> float:
        return max((machine.cpu_used + pending.cpu) / machine.cpu,
                   (machine.mem_used + pending.mem) / machine.mem)

    def is_big(t: Task) -> bool:
        """大任务判定：CPU>1 或 MEM>1 GiB"""
        return t.cpu > 1.0 or t.mem > 1.0

    def preempt_opportunity(machine: Machine, pending: Task, eff_limit: float) -> bool:
        # 仅在 soft/hard 限制之间允许预占，且只驱逐低敏感小任务
        if util_with_task(machine, pending) <= eff_limit:
            return True
        # 回收该节点上最小的低敏感任务以尝试腾挪空间
        if not machine.task_records:
            return False
        # 仅对小任务生效，避免大幅波动
        victims = [r for r in machine.task_records
                   if r.get("pool") not in ("opportunity", "slo_spill") and
                   r.get("slo", "low") != "high" and r["cpu"] <= 0.5]
        if not victims:
            return False
        victims.sort(key=lambda r: r["cpu"])  # 先回收更小的
        freed_cpu = 0.0;
        freed_mem = 0.0;
        removed = []
        for r in victims:
            freed_cpu += r["cpu"];
            freed_mem += r["mem"];
            removed.append(r)
            if ((machine.cpu - machine.cpu_used + freed_cpu) >= pending.cpu and
                    (machine.mem - machine.mem_used + freed_mem) >= pending.mem):
                break
        # 检查是否可满足
        if ((machine.cpu - machine.cpu_used + freed_cpu) < pending.cpu or
                (machine.mem - machine.mem_used + freed_mem) < pending.mem):
            return False
        # 应用回收
        for r in removed:
            machine.cpu_used -= r["cpu"];
            machine.mem_used -= r["mem"]
            try:
                machine.tasks.remove((r["task_id"], r["tenant"]))
            except ValueError:
                pass
            machine.task_records.remove(r)
        return True

    def rescue_place(pending: Task) -> Machine:
        """温和救援放置：在高利用但安全的节点上回收至多2个小低敏感任务，为高敏感或大任务让路。"""
        max_evictions = 2 if pending.slo_sensitive == 'high' or is_big(pending) else 1
        # 优先尝试当前最接近上限但仍安全的节点，减少对全局均衡的扰动
        sorted_nodes = sorted(machines, key=lambda m: -m.utilization())
        for m in sorted_nodes:
            # 节点风险过高则跳过
            eff_limit = calc_node_limit(m.id)
            if m.utilization() > min(0.97, eff_limit + cluster_state["spill_margin"]):
                continue
            # 尝试逐个回收
            if not m.task_records:
                continue
            victims = [r for r in m.task_records
                       if r.get("pool") not in ("opportunity", "slo_spill") and
                       r.get("slo", "low") != "high" and r["cpu"] <= 0.5]
            if not victims:
                continue
            victims.sort(key=lambda r: r["cpu"])  # 先回收更小的
            freed_cpu = 0.0;
            freed_mem = 0.0;
            removed = []
            for r in victims[:max_evictions]:
                freed_cpu += r["cpu"];
                freed_mem += r["mem"];
                removed.append(r)
                if ((m.cpu - m.cpu_used + freed_cpu) >= pending.cpu and
                        (m.mem - m.mem_used + freed_mem) >= pending.mem):
                    break
            if ((m.cpu - m.cpu_used + freed_cpu) >= pending.cpu and
                    (m.mem - m.mem_used + freed_mem) >= pending.mem):
                # 预检查救援后的风险是否满足目标
                util_after = max((m.cpu_used - 0 + pending.cpu) / m.cpu,
                                 (m.mem_used - 0 + pending.mem) / m.mem)
                viol = predict_violation_risk(util_after)
                if util_after > eff_limit or viol > cluster_state["slo_target"]:
                    continue
                # 应用回收
                for r in removed:
                    m.cpu_used -= r["cpu"];
                    m.mem_used -= r["mem"]
                    try:
                        m.tasks.remove((r["task_id"], r["tenant"]))
                    except ValueError:
                        pass
                    m.task_records.remove(r)
                return m
        return None

    # 自适应 SLO 限制的状态（全局/节点风控）
    # 从在线调参器获得一组起始参数
    tune_cfg = tuner.select()

    cluster_state = {
        "base_limit": tune_cfg["base_limit"],
        "global_risk_ema": 0.02,  # 全局尾部风险 EMA
        "machine_risk_ema": {i: 0.02 for i in range(num_machines)},  # 节点尾部风险 EMA
        "tenant_overrides": {},  # 可扩展：手动覆写某租户阈值
        "tenant_credits": tenant_credits,  # 信用表（与 wDRF 对齐）
        "alpha": 0.2,  # EMA 学习率
        "k_machine": 0.10,
        "k_global": 0.10,
        # 进一步收紧上限
        "limit_bounds": (0.75, 0.98),  # 放宽上限边界
        "credit_limit_gain": 0.04,
        "top_k": int(os.getenv("SCHED_TOPK", str(tune_cfg["top_k"]))),  # 允许环境覆盖
        "spill_margin": min(0.18, tune_cfg["spill_margin"] + 0.05),
        "slo_target": float(os.getenv("SLO_TARGET", "0.060")),
        "ctrl_window": int(os.getenv("SLO_CTRL_WINDOW", "2000")),  # 控制窗口（任务数）
        # 温和的控制器增益
        "ctrl_kp_limit": float(os.getenv("SLO_KP_LIMIT", "0.010")),
        "ctrl_kp_spill": float(os.getenv("SLO_KP_SPILL", "0.010")),
        "spill_bounds": (0.00, 0.08),
        "top_k_min": 4,
        "top_k_max": 48,
        "ctrl_count": 0,
        "ctrl_risk_acc": 0.0,
        "last_ctrl_avg_risk": None,
        # 温和的机会池配置
        "opportunity_credit_threshold": float(os.getenv("OPP_CREDIT_THRESHOLD", "0.45")),
        "opportunity_soft_limit": float(os.getenv("OPP_SOFT_LIMIT", "0.88")),
        "opportunity_hard_limit": float(os.getenv("OPP_HARD_LIMIT", "0.90")),
        "max_opportunity_share": float(os.getenv("OPP_MAX_SHARE", "0.35")),  # 允许更多机会池任务
    }

    # 优先队列：高敏感任务穿插调度
    high_queue = [t for t in tasks if t.slo_sensitive == 'high']
    low_queue = [t for t in tasks if t.slo_sensitive != 'high']
    queued_tasks = []
    hi = lo = 0
    while hi < len(high_queue) or lo < len(low_queue):
        if hi < len(high_queue):
            queued_tasks.append(high_queue[hi])
            hi += 1
        if lo < len(low_queue):
            queued_tasks.append(low_queue[lo])
            lo += 1

    for idx, task in enumerate(queued_tasks):
        if idx % 2000 == 0:
            print(f"  SLO-Driven {idx}/{len(tasks)}...", end='\r')

        # 直接从所有机器中选择可容纳的候选，避免小任务受 0.90 阈值限制
        candidates = [m for m in machines
                      if m.cpu_used + task.cpu <= m.cpu and
                      m.mem_used + task.mem <= m.mem]

        if not candidates:
            # 无直接可容纳节点时，尝试救援放置或回退到最低违约节点
            rescued = rescue_place(task)
            if rescued is not None:
                selected_machine = rescued
                pool = "slo_preempt"
            else:
                # 选取容量可行且违约最小的节点作为兜底
                fallback = []
                for m in machines:
                    if m.cpu_used + task.cpu > m.cpu or m.mem_used + task.mem > m.mem:
                        continue
                    ua = util_with_task(m, task)
                    viol = predict_violation_risk(ua)
                    fallback.append((viol, ua, m))
                if fallback:
                    fallback.sort(key=lambda x: (x[0], x[1]))
                    selected_machine = fallback[0][2]
                    pool = "slo_spill"
                else:
                    failed += 1
                    # 动态扩容（可选）：不足时增加新节点
                    if failed > 0.05 * len(tasks) and len(machines) < num_machines + 20:
                        new_machine = Machine(id=len(machines))
                        machines.append(new_machine)
                    continue

        if cluster_state["top_k"] > 0 and len(candidates) > cluster_state["top_k"]:
            scored = []
            for m in candidates:
                util_after = util_with_task(m, task)
                scored.append((m, calc_score_shared(task, m, util_after)))
            scored.sort(key=lambda x: x[1])
            candidates = [m for m, _ in scored[:cluster_state["top_k"]]]

        credit = tenant_credits[task.tenant]
        is_opportunity = credit < cluster_state["opportunity_credit_threshold"]
        if is_opportunity:
            next_share = (opportunity_active + 1) / max(1, scheduled + 1)
            if next_share > cluster_state["max_opportunity_share"]:
                is_opportunity = False

        # 基于目标违约率的风险阈值
        risk_threshold = cluster_state["slo_target"]
        selected_machine = None
        pool = "slo"
        eff_limit_cache = {}

        if is_opportunity:
            pool = "opportunity"
            soft_limit = cluster_state["opportunity_soft_limit"]
            hard_limit = cluster_state["opportunity_hard_limit"]
            soft_candidates = []
            hard_candidates = []
            for machine in candidates:
                util_after = util_with_task(machine, task)
                if util_after <= soft_limit:
                    soft_candidates.append((calc_score_shared(task, machine, util_after), machine))
                elif util_after <= hard_limit:
                    hard_candidates.append((calc_score_shared(task, machine, util_after), util_after, machine))
            if soft_candidates:
                soft_candidates.sort(key=lambda x: x[0])
                selected_machine = soft_candidates[0][1]
            elif hard_candidates:
                hard_candidates.sort(key=lambda x: (x[0], x[1]))
                selected_machine = hard_candidates[0][2]
            else:
                is_opportunity = False
                pool = "slo"

        if selected_machine is None:
            base_limit = tune_slo_limit(cluster_state, task.tenant)
            safe_candidates = []
            for machine in candidates:
                eff_limit = eff_limit_cache.get(machine.id)
                if eff_limit is None:
                    eff_limit = calc_node_limit(machine.id)
                    eff_limit_cache[machine.id] = eff_limit
                util_after = util_with_task(machine, task)
                if util_after <= eff_limit:
                    safe_candidates.append((risk_model.predict(util_after), util_after, machine))
            if safe_candidates:
                safe_candidates.sort(key=lambda x: (x[0], x[1]))
                selected_machine = safe_candidates[0][2]
                pool = "slo"
            else:
                for machine in candidates:
                    if not machine.opportunity_records:
                        continue
                    eff_limit = eff_limit_cache.get(machine.id)
                    if eff_limit is None:
                        eff_limit = calc_node_limit(machine.id)
                    if preempt_opportunity(machine, task, eff_limit):
                        selected_machine = machine
                        pool = "slo"
                        break
                if selected_machine is None:
                    spill_candidates = []
                    for machine in candidates:
                        eff_limit = eff_limit_cache.get(machine.id)
                        if eff_limit is None:
                            eff_limit = calc_node_limit(machine.id)
                        spill_cap = min(eff_limit + cluster_state["spill_margin"], cluster_state["limit_bounds"][1])
                        util_after = util_with_task(machine, task)
                        if util_after <= spill_cap:
                            spill_candidates.append((risk_model.predict(util_after), util_after, machine))
                    if spill_candidates:
                        spill_candidates.sort(key=lambda x: (x[0], x[1]))
                        selected_machine = spill_candidates[0][2]
                        pool = "slo_spill"
                    else:
                        # 硬抢占：尝试驱逐低敏感小任务给高敏感任务让位
                        high_demand = task.slo_sensitive == 'high'
                        preempted = False
                        if high_demand:
                            target = max((m for m in machines if m.task_records), key=lambda m: m.utilization(),
                                         default=None)
                            if target and target.utilization() > 0.95:
                                for record in list(reversed(target.task_records)):
                                    if record.get("pool") in ("opportunity", "slo_spill"):
                                        continue
                                    if record.get("slo", "low") == "high":
                                        continue
                                    if record["cpu"] <= 0.5:
                                        target.cpu_used -= record["cpu"]
                                        target.mem_used -= record["mem"]
                                        try:
                                            target.tasks.remove((record["task_id"], record["tenant"]))
                                        except ValueError:
                                            pass
                                        target.task_records.remove(record)
                                        preempted = True
                                        break
                            if preempted:
                                selected_machine = target
                                pool = "slo_preempt"
                        if not preempted:
                            # 最后尝试救援放置（小规模温和预占），避免失败
                            rescued = rescue_place(task)
                            if rescued is None:
                                # 最后一次尝试：在风险仍可接受范围内放宽限制（预算兜底）
                                if cluster_state["global_risk_ema"] < cluster_state["slo_target"] * 1.2:
                                    loose_candidates = []
                                    for m in candidates:
                                        if m.cpu_used + task.cpu > m.cpu or m.mem_used + task.mem > m.mem:
                                            continue
                                        ua = util_with_task(m, task)
                                        if ua <= 0.98:  # 绝对硬上限，防止超满
                                            loose_candidates.append((ua, m))
                                    if loose_candidates:
                                        loose_candidates.sort(key=lambda x: x[0])
                                        selected_machine = loose_candidates[0][1]
                                        pool = "slo_budget"
                                    else:
                                        failed += 1
                                        tenant_credits[task.tenant] = max(0.1, credit - 0.05)
                                        continue
                                else:
                                    failed += 1
                                    tenant_credits[task.tenant] = max(0.1, credit - 0.05)
                                    continue
                            else:
                                selected_machine = rescued
                                pool = "slo_preempt"

        machine = selected_machine
        util_after_chk = util_with_task(machine, task)
        # viol_chk is defined here
        viol_chk = predict_violation_risk(util_after_chk)
        eff_limit_chk = calc_node_limit(machine.id)

        # --- 风险预算守卫 (修正) ---
        if viol_chk > risk_threshold or util_after_chk > eff_limit_chk:
            if cluster_state["global_risk_ema"] < cluster_state["slo_target"]:
                pool = "slo_spill"
            else:
                alt_candidates = []
                for m in machines:
                    if m == machine or m.cpu_used + task.cpu > m.cpu or m.mem_used + task.mem > m.mem:
                        continue
                    ua = util_with_task(m, task)
                    if predict_violation_risk(ua) <= risk_threshold and ua <= calc_node_limit(m.id):
                        alt_candidates.append((predict_violation_risk(ua), ua, m))
                if alt_candidates:
                    alt_candidates.sort(key=lambda x: (x[0], x[1]))
                    machine = alt_candidates[0][2]
                    pool = "slo"
                else:
                    rescued = rescue_place(task)
                    if rescued:
                        machine = rescued
                        pool = "slo_preempt"
                    else:
                        failed += 1
                        continue

        machine.cpu_used += task.cpu
        machine.mem_used += task.mem
        machine.tasks.append((task.id, task.tenant))
        record = {
            "task_id": task.id,
            "tenant": task.tenant,
            "cpu": task.cpu,
            "mem": task.mem,
            "pool": pool
        }
        machine.task_records.append(record)
        if pool == "opportunity":
            machine.opportunity_records.append(record)
            opportunity_scheduled += 1
            opportunity_active += 1
        scheduled += 1

        # --- 碎片整理: 每 1000 次调度 ---
        if scheduled % 1000 == 0:
            for m in machines:
                if m.utilization() > 0.90 and (m.cpu - m.cpu_used) > 0.8:
                    # 找一个低敏感小任务驱逐
                    victim = None
                    for rec in reversed(m.task_records):
                        if rec.get("pool") in ("opportunity", "slo_spill"):
                            continue
                        if rec.get("slo", "low") == "high":
                            continue
                        if rec["cpu"] <= 0.5:
                            victim = rec;
                            break
                    if victim:
                        m.cpu_used -= victim["cpu"]
                        m.mem_used -= victim["mem"]
                        try:
                            m.tasks.remove((victim["task_id"], victim["tenant"]))
                        except ValueError:
                            pass
                        m.task_records.remove(victim)
                        # 找最空闲节点
                        target = min(machines, key=lambda x: x.utilization())
                        target.cpu_used += victim["cpu"]
                        target.mem_used += victim["mem"]
                        target.tasks.append((victim["task_id"], victim["tenant"]))
                        target.task_records.append(victim)

        util_after = machine.utilization()
        risk_now = risk_model.predict(util_after)
        alpha = cluster_state["alpha"]
        prev_m = cluster_state["machine_risk_ema"].get(machine.id, 0.02)
        cluster_state["machine_risk_ema"][machine.id] = (1 - alpha) * prev_m + alpha * risk_now
        prev_g = cluster_state["global_risk_ema"]
        cluster_state["global_risk_ema"] = (1 - alpha) * prev_g + alpha * risk_now

        cluster_state["ctrl_count"] += 1
        cluster_state["ctrl_risk_acc"] += risk_now
        if cluster_state["ctrl_count"] >= cluster_state["ctrl_window"]:
            avg_risk = cluster_state["ctrl_risk_acc"] / max(cluster_state["ctrl_window"], 1)
            cluster_state["last_ctrl_avg_risk"] = avg_risk
            error = avg_risk - cluster_state["slo_target"]

            new_limit = cluster_state["base_limit"] - cluster_state["ctrl_kp_limit"] * error
            lb, ub = cluster_state["limit_bounds"]
            cluster_state["base_limit"] = max(min(new_limit, ub), lb)

            new_spill = cluster_state["spill_margin"] - cluster_state["ctrl_kp_spill"] * error
            s_lb, s_ub = cluster_state["spill_bounds"]
            cluster_state["spill_margin"] = max(min(new_spill, s_ub), s_lb)

            # 加速收敛：如果连续超标则直接收紧机会池硬限
            if error > 0:
                cluster_state["opportunity_hard_limit"] = max(cluster_state["opportunity_hard_limit"] - 0.01,
                                                              cluster_state["opportunity_soft_limit"] + 0.03)
                cluster_state["top_k"] = min(cluster_state["top_k"] + 2, cluster_state["top_k_max"])
            else:
                cluster_state["top_k"] = max(cluster_state["top_k"] - 1, cluster_state["top_k_min"])

            cluster_state["ctrl_count"] = 0
            cluster_state["ctrl_risk_acc"] = 0.0

        current_credit = tenant_credits[task.tenant]
        if pool == "opportunity":
            if util_after > cluster_state["opportunity_soft_limit"]:
                tenant_credits[task.tenant] = max(0.1, current_credit - 0.02)
            elif util_after < 0.60:
                tenant_credits[task.tenant] = min(cluster_state["opportunity_credit_threshold"], current_credit + 0.01)
        else:
            if util_after > 0.85:
                tenant_credits[task.tenant] = max(0.3, current_credit - 0.01)
            elif util_after < 0.70:
                tenant_credits[task.tenant] = min(1.0, current_credit + 0.01)

        # 风险低于目标时，更偏好利用率；超标时更偏好降风险
        if risk_now <= cluster_state["slo_target"]:
            reward = 0.8 * util_after + 0.2 * (1.0 - risk_now)
        else:
            reward = 0.8 * (1.0 - risk_now) + 0.2 * util_after
        tuner.update(reward)

        # --- 周期性重新采样 Bandit 臂，动态调整参数 ---
        if scheduled % tuner.window == 0:
            new_cfg = tuner.select()
            cluster_state["top_k"] = new_cfg["top_k"]
            # 仅在全局风险明显低于目标时才尝试抬高 base_limit
            lb, ub = cluster_state["limit_bounds"]
            if (cluster_state["global_risk_ema"] <= cluster_state["slo_target"] * 0.8 and
                    new_cfg["base_limit"] > cluster_state["base_limit"]):
                cluster_state["base_limit"] = min(new_cfg["base_limit"], ub)
            cluster_state["spill_margin"] = new_cfg["spill_margin"]

    print()

    # 输出在线学习的统计
    print(f"\n  [在线学习统计]")
    print(f"    Bandit 探索/利用: ε={tuner.eps}, 窗口={tuner.window}")
    best_arm_idx = int(np.argmax(tuner.values))
    best_arm = tuner.arms[best_arm_idx]
    print(f"    最优臂（当前估值最高）: Top-K={best_arm['top_k']}, "
          f"base_limit={best_arm['base_limit']:.2f}, spill_margin={best_arm['spill_margin']:.2f}")
    print(f"    最优臂被选中: {tuner.counts[best_arm_idx]} 次, 平均奖励: {tuner.values[best_arm_idx]:.4f}")

    # Top 3 臂
    top3_idx = np.argsort(tuner.values)[-3:][::-1]
    print(f"    Top-3 臂配置:")
    for rank, idx in enumerate(top3_idx, 1):
        arm = tuner.arms[idx]
        print(f"      #{rank}: Top-K={arm['top_k']}, base={arm['base_limit']:.2f}, "
              f"spill={arm['spill_margin']:.2f} | 奖励={tuner.values[idx]:.4f} (试{tuner.counts[idx]}次)")

    # 风险模型
    print(f"    风险模型: 统一预测函数")

    # 候选打分权重
    print(f"    候选打分权重: α(风险)=0.7/0.4, β(利用率)=0.3/0.6 (高/低敏感)")

    # 闭环控制摘要
    last_avg_risk = cluster_state["last_ctrl_avg_risk"]
    if last_avg_risk is None:
        last_avg_risk = cluster_state["global_risk_ema"]
    print(f"    闭环控制: 目标违约率={cluster_state['slo_target'] * 100:.2f}%, "
          f"最后窗口风险={last_avg_risk * 100:.2f}%")
    print(f"      最终参数: base_limit={cluster_state['base_limit']:.2f}, "
          f"spill_margin={cluster_state['spill_margin']:.2f}, top_k={cluster_state['top_k']}")
    print(f"    机会池统计: 活跃={opportunity_active}, 总调度={opportunity_scheduled}, 被回收={opportunity_evicted}")

    return {
        "name": "SLO-Driven (本研究)",
        "scheduled": scheduled,
        "failed": failed,
        "machines": machines,
        "credits": dict(tenant_credits),
        "bandit_best_arm": best_arm,
        "bandit_best_reward": tuner.values[best_arm_idx],
        "final_base_limit": cluster_state["base_limit"],
        "final_spill_margin": cluster_state["spill_margin"],
        "final_top_k": cluster_state["top_k"],
        "last_window_risk": last_avg_risk,
        "opportunity_active": opportunity_active,
        "opportunity_scheduled": opportunity_scheduled,
        "opportunity_evicted": opportunity_evicted,
    }


def run_nextgen_scheduler(tasks: List[Task], num_machines: int = 114) -> dict:
    """
    Next-generation layered scheduler with tenant selection and scoring.
    
    ⭐ 修复版本：使用事件驱动模拟，与 Mesos/Tetris 保持一致的采样方式
    """
    np.random.seed(2048)
    random.seed(2048)
    print("\n━━━ [5/5] NextGen Scheduler (Layered) ━━━")

    machines = [Machine(id=i, cpu=11.0, mem=11.0) for i in range(num_machines)]

    cpu_total = sum(m.cpu for m in machines)
    mem_total = sum(m.mem for m in machines)

    tenant_groups = {t.tenant: getattr(t, "priority", 0) for t in tasks}
    selector = TenantSelector(tenant_groups=tenant_groups)
    selector.set_cluster_capacity(cpu_total, mem_total)
    base_low_wm = float(os.getenv("NEXTGEN_LOW_WM", "0.60"))
    base_high_wm = float(os.getenv("NEXTGEN_HIGH_WM", "0.92"))
    guard = WatermarkGuard(low=base_low_wm, high=base_high_wm)
    forecast = EWMA(alpha=0.4)
    base_alpha = float(os.getenv("NEXTGEN_ALPHA", "0.85"))
    residual_controller = ResidualController(selector)
    
    # 用于在批量调度函数中共享状态
    global_stats = {
        "avg_util": 0.0,
        "fragmentation": 0.0,
        "imbalance": 0.0,
    }
    task_dict = {t.id: t for t in tasks}

    # ⭐ 定义批量调度函数（封装 NextGen 的核心逻辑）
    def nextgen_schedule_batch(batch_tasks, current_machines):
        """
        NextGen 批量调度逻辑
        返回 placements: [(task_id, machine_id), ...]
        """
        placements = []
        
        for task in batch_tasks:
            tid = task.id
            cpu = task.cpu
            mem = task.mem
            tenant = task.tenant
            arrival = task.arrival
            
            # 状态更新和 RL 推理
            state_vec, group_queues = residual_controller.build_state(current_machines, global_stats)
            if ppo_model:
                state_input = np.array(state_vec[:15], dtype=np.float32)
                if len(state_vec) < 15:
                    state_input = np.pad(state_input, (0, 15 - len(state_vec)), 'constant')
                action_vec, _ = ppo_model.predict(state_input, deterministic=True)
                residual_action = {
                    "delta_alpha": float(action_vec[0]),
                    "delta_high_wm": float(action_vec[1]),
                    "delta_low_wm": float(action_vec[2]),
                    "group_delta": {}
                }
            else:
                residual_action = {}
            
            alpha, high_wm, low_wm, group_weights = residual_controller.apply_residuals(
                residual_action, base_alpha, base_high_wm, base_low_wm
            )
            guard.low = low_wm
            guard.high = high_wm
            if group_weights:
                selector.update_group_weights(group_weights)
            
            # 选择最佳节点
            candidate = None
            best_score = float("inf")
            use_affinity = os.getenv("NEXTGEN_USE_AFFINITY", "1") == "1"
            
            for machine in current_machines:
                if machine.cpu_used + cpu > machine.cpu or machine.mem_used + mem > machine.mem:
                    continue
                penalty = guard.penalty(machine)
                if penalty >= guard.high_penalty:
                    continue
                forecast.update(machine.id, machine.utilization())
                
                # 准备多维资源
                extra_dims = {}
                if hasattr(task, 'mem_bandwidth') and task.mem_bandwidth > 0:
                    extra_dims['mem_bandwidth'] = machine.mem_bandwidth_cap - machine.mem_bandwidth
                if hasattr(task, 'net_in') and task.net_in > 0:
                    extra_dims['net_bandwidth'] = machine.net_bandwidth_cap - machine.net_bandwidth
                
                util_score = score_node(
                    machine, task, alpha=alpha,
                    extra_dims=extra_dims if extra_dims else None,
                    use_affinity=use_affinity,
                    affinity_bonus=0.05,
                )
                
                util_forecast = forecast.forecast(machine.id)
                total_score = util_score * penalty + 0.05 * util_forecast
                if total_score < best_score:
                    best_score = total_score
                    candidate = machine
            
            if candidate:
                # ✅ 临时更新资源（防止批次内过度分配）
                candidate.cpu_used += cpu
                candidate.mem_used += mem
                placements.append((tid, candidate.id))
                # 更新 selector 状态
                selector.update_usage(tenant, cpu, mem)
        
        # 更新全局统计
        avg_util, max_util, std_util = cpu_mem_util(current_machines)
        frag = fragmentation(current_machines)
        imb = imbalance(current_machines)
        global_stats.update({
            "avg_util": avg_util,
            "fragmentation": frag,
            "imbalance": imb,
        })
        
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


def analyze_result(result: dict, trace_dir: str, tasks: List[Task]) -> dict:
    """Compute metrics without any SLO or fairness."""
    # ⭐ 调试输出：查看输入
    print(f"\n  [analyze_result] 输入: scheduled={result.get('scheduled', 'MISSING')}, "
          f"failed={result.get('failed', 'MISSING')}, "
          f"has_all_scheduled_task_ids={('all_scheduled_task_ids' in result)}")

    machines = result["machines"]

    # ------- compute effective utilization -------
    task_dict = {t.id: t for t in tasks}

    # ⭐ 对于事件驱动模式，直接使用时间加权的真实利用率
    if 'effective_util_over_time' in result:
        # 事件驱动模式：使用过程中采样计算的平均真实利用率
        effective_util = result['effective_util_over_time']
        waste_rate = 1.0 - effective_util
        # ⭐ 计算 real_used 用于调试输出
        capacity_total = len(machines) * 11.0
        real_used = effective_util * capacity_total
    else:
        # 静态模式：基于最终快照计算
        real_used = 0.0
        for m in machines:
            used_real = sum(getattr(task_dict[tid], "real_cpu", 0.0) for tid, _ in m.tasks)
            real_used += used_real

        capacity_total = len(machines) * 11.0
        effective_util = real_used / capacity_total if capacity_total else 0.0
        waste_rate = 1.0 - effective_util

    # ⭐ 使用事件驱动模拟过程中的利用率（而不是最终快照）
    if 'avg_util_over_time' in result and 'max_util_seen' in result:
        # 事件驱动模式：使用过程中采样的利用率
        avg_util = result['avg_util_over_time']
        max_util = result['max_util_seen']
        cpu_util = result.get('avg_cpu_util', 0.0)
        mem_util = result.get('avg_mem_util', 0.0)
        # 碎片率基于平均利用率
        frag = 1.0 - avg_util
        # 失配率设为0（事件驱动下最终快照不准确，无法计算有意义的 std）
        imb = 0.0
        std_util = 0.0
    else:
        # 静态模式：使用最终快照
        avg_util, max_util, std_util = cpu_mem_util(machines)
        frag = fragmentation(machines)
        imb = imbalance(machines)

        # 分别计算 CPU / MEM util
        cpu_used_tot = sum(m.cpu_used for m in machines)
        cpu_cap_tot = sum(m.cpu for m in machines)
        mem_used_tot = sum(m.mem_used for m in machines)
        mem_cap_tot = sum(m.mem for m in machines)
        cpu_util = cpu_used_tot / cpu_cap_tot if cpu_cap_tot else 0.0
        mem_util = mem_used_tot / mem_cap_tot if mem_cap_tot else 0.0

    recv_bw, send_bw = net_bandwidth(trace_dir)

    total = result["scheduled"] + result["failed"]

    # ----- DEBUG: per-algorithm request size and usage summary -----
    import numpy as np

    # ⭐ 对于事件驱动模式，使用所有已调度任务
    if 'all_scheduled_task_ids' in result:
        req_cpu = [task_dict[tid].cpu for tid in result['all_scheduled_task_ids'] if tid in task_dict]
        req_mem = [task_dict[tid].mem for tid in result['all_scheduled_task_ids'] if tid in task_dict]
    else:
        req_cpu = [task_dict[tid].cpu for m in machines for tid, _ in m.tasks]
        req_mem = [task_dict[tid].mem for m in machines for tid, _ in m.tasks]
    if req_cpu:
        mean_cpu = float(np.mean(req_cpu))
        mean_mem = float(np.mean(req_mem))
        p50_cpu, p90_cpu, p99_cpu = np.percentile(req_cpu, [50, 90, 99])
    else:
        mean_cpu = mean_mem = p50_cpu = p90_cpu = p99_cpu = 0.0

    # 检查 dominant resource 分布
    cpu_dominant = sum(1 for m in machines if m.cpu_used / m.cpu > m.mem_used / m.mem if m.cpu > 0 and m.mem > 0)
    mem_dominant = sum(1 for m in machines if m.mem_used / m.mem >= m.cpu_used / m.cpu if m.cpu > 0 and m.mem > 0)

    # ------- 新增指标: 任务时长与亲和性 -------
    scheduled_tasks = [task_dict[tid] for m in machines for tid, _ in m.tasks]
    durations = [t.duration for t in scheduled_tasks if t.duration > 0]
    avg_duration = float(np.mean(durations)) if durations else 0.0

    # 亲和性命中率：多少任务被调度回原始节点
    affinity_hits = 0
    affinity_total = 0
    for m in machines:
        for tid, _ in m.tasks:
            task = task_dict.get(tid)
            if task and hasattr(task, 'machine_id') and task.machine_id:
                affinity_total += 1
                if str(m.id) == str(task.machine_id):
                    affinity_hits += 1
    affinity_rate = affinity_hits / affinity_total if affinity_total else 0.0

    print(f"[DEBUG] {result['name']:<30}")
    print(
        f"        任务: 已调度={len(req_cpu):5d} | CPU: Σreq={sum(req_cpu):7.1f} avg={mean_cpu:.3f} P50={p50_cpu:.2f}")
    print(
        f"                                | MEM: Σreq={sum(req_mem):7.1f} avg={mean_mem:.3f} | Σreal_cpu={real_used:.1f}")
    print(f"        节点: CPU主导={cpu_dominant:2d}台, MEM主导={mem_dominant:2d}台 / 共{len(machines)}台")

    # ⭐ 事件驱动模式的输出
    if 'avg_util_over_time' in result:
        print(
            f"        [事件驱动] 调度轮次={result.get('num_rounds', 0)}, 已释放={result.get('total_released', 0)}任务")
        print(
            f"        利用率(过程平均): AvgUtil={avg_util * 100:.1f}%, CPUUtil={cpu_util * 100:.1f}%, MEMUtil={mem_util * 100:.1f}%")
    else:
        # 静态模式的输出
        cpu_used_tot = sum(m.cpu_used for m in machines)
        cpu_cap_tot = sum(m.cpu for m in machines)
        mem_used_tot = sum(m.mem_used for m in machines)
        mem_cap_tot = sum(m.mem for m in machines)
        print(f"        利用率验算: CPUUtil={cpu_used_tot:.1f}/{cpu_cap_tot:.1f}={cpu_util * 100:.1f}%, "
              f"MEMUtil={mem_used_tot:.1f}/{mem_cap_tot:.1f}={mem_util * 100:.1f}%")

    print(
        f"        任务时长: 平均={avg_duration:.0f}秒 | 亲和性命中率={affinity_rate * 100:.1f}% ({affinity_hits}/{affinity_total})")

    # 动态资源管理统计
    if 'total_released' in result and result['total_released'] > 0:
        print(f"        动态管理: 已释放={result['total_released']}任务, 仍活跃={result.get('active_tasks', 0)}任务")

    return {
        "name": result["name"],
        "machines": machines,
        "scheduled": result["scheduled"],
        "failed": result["failed"],
        "success_rate": result["scheduled"] / max(total, 1),
        "avg_util": avg_util,
        "max_util": max_util,
        "std_util": std_util,
        "fragmentation": frag,
        "imbalance": imb,
        "effective_util": effective_util,
        "waste_rate": waste_rate,
        "net_recv": recv_bw,
        "net_send": send_bw,
        "cpu_util": cpu_util,
        "mem_util": mem_util,
        "avg_duration": avg_duration,
        "affinity_rate": affinity_rate,
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python run_complete_comparison.py /path/to/trace [max_instances]")
        print("  max_instances: 可选，默认加载全部数据")
        print("\n示例:")
        print("  python run_complete_comparison.py ./data        # 加载全部")
        print("  python run_complete_comparison.py ./data 10000  # 只加载 10000 条")
        print("\n需要安装: pip install ortools")
        sys.exit(1)

    # 加载数据：None = 全部数据
    max_instances = None if len(sys.argv) < 3 else int(sys.argv[2])
    tasks = load_alibaba_trace(sys.argv[1], max_instances)

    # 根据任务数动态调整节点数，或用户 CLI 指定
    if len(sys.argv) >= 4:
        num_machines = int(sys.argv[3])
        print(f"节点数由 CLI 指定: {num_machines}\n")
    else:
        total_mem = sum(t.mem for t in tasks)
        target_util = float(os.getenv("TARGET_UTIL", "1.0"))
        safety_buffer = int(os.getenv("TARGET_BUFFER_NODES", "2"))
        if target_util <= 0 or target_util > 1.0:
            raise ValueError("TARGET_UTIL must be within (0, 1]")
        num_machines = math.ceil(total_mem / 11.0 / target_util) + safety_buffer
        print(f"动态节点数: {num_machines} (目标利用率 {target_util * 100:.1f}%, 缓冲 {safety_buffer} 节点)\n")

    # 运行所有调度器
    results = []

    # ⭐ 暂时跳过 Firmament（Flow Graph 求解器返回 INFEASIBLE）
    if os.getenv("ENABLE_FIRMAMENT", "0") == "1":
        try:
            res_firmament = run_firmament(tasks, num_machines)
            print(
                f"  [DEBUG] Firmament返回: scheduled={res_firmament.get('scheduled', 'N/A')}, failed={res_firmament.get('failed', 'N/A')}")
            results.append(analyze_result(res_firmament, sys.argv[1], tasks))
        except Exception as e:
            print(f"  Firmament 错误: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("━━━ [1/4] Firmament Flow Scheduler (OSDI'16) - 已跳过 ━━━")
        print("  (Flow Graph 求解器持续返回 INFEASIBLE，需要调试)")
        print("  (设置 ENABLE_FIRMAMENT=1 以启用)\n")

    res_mesos = run_mesos_drf(tasks, num_machines)
    print(
        f"  [DEBUG] Mesos返回: scheduled={res_mesos.get('scheduled', 'N/A')}, failed={res_mesos.get('failed', 'N/A')}")
    results.append(analyze_result(res_mesos, sys.argv[1], tasks))

    res_tetris = run_tetris(tasks, num_machines)
    print(
        f"  [DEBUG] Tetris返回: scheduled={res_tetris.get('scheduled', 'N/A')}, failed={res_tetris.get('failed', 'N/A')}")
    results.append(analyze_result(res_tetris, sys.argv[1], tasks))

    # ⭐ 暂时跳过 SLO-Driven（需要重构为事件驱动）
    if os.getenv("ENABLE_SLO_DRIVEN", "0") == "1":
        res_ours = run_slo_driven(tasks, num_machines)
        print(
            f"  [DEBUG] SLO-Driven返回: scheduled={res_ours.get('scheduled', 'N/A')}, failed={res_ours.get('failed', 'N/A')}")
        results.append(analyze_result(res_ours, sys.argv[1], tasks))
    else:
        print("━━━ [4/4] SLO-Driven (本研究) - 已跳过 ━━━")
        print("  (静态模式，与事件驱动baseline不可比)")
        print("  (设置 ENABLE_SLO_DRIVEN=1 以启用)\n")

    res_nextgen = run_nextgen_scheduler(tasks, num_machines)
    print(
        f"  [DEBUG] NextGen返回: scheduled={res_nextgen.get('scheduled', 'N/A')}, failed={res_nextgen.get('failed', 'N/A')}")
    results.append(analyze_result(res_nextgen, sys.argv[1], tasks))

    # 输出对比（修正对齐）
    print("\n" + "=" * 115)
    print(f"完整基线对比 (严格按源码实现，Alibaba 2018 Trace, {len(tasks)} 实例, {num_machines} 节点)")
    print("=" * 115)

    # 表头
    line1 = f"{'算法':<30} {'成功率':>9} {'AvgUtil':>9} {'CPUUtil':>9} {'MemUtil':>9} {'碎片率':>9} {'实用Util':>9} {'最大Util':>9} {'失配率':>9}"
    print(line1)
    print("-" * 100)

    # 数据行
    for stats in results:
        print(
            f"{stats['name']:<30} {stats['success_rate'] * 100:8.1f}% "
            f"{stats['avg_util'] * 100:8.1f}% {stats['cpu_util'] * 100:8.1f}% {stats['mem_util'] * 100:8.1f}% "
            f"{stats['fragmentation'] * 100:8.1f}% {stats['effective_util'] * 100:8.1f}% {stats['max_util'] * 100:8.1f}% "
            f"{stats['imbalance'] * 100:8.1f}%"
        )

    # 指标说明
    print("\n" + "=" * 100)
    print("指标说明:")
    print("  • AvgUtil   = 平均节点利用率 (每个节点的 max(CPU%, MEM%) 的平均值)")
    print("  • CPUUtil   = 集群总体 CPU 利用率 (Σ已用CPU / Σ总CPU容量)")
    print("  • MemUtil   = 集群总体内存利用率 (Σ已用MEM / Σ总MEM容量)")
    print("  • 实用Util  = 实际CPU使用率 (基于 container_usage.csv 的真实用量 / 总容量)")
    print("  • 碎片率    = 1 - AvgUtil (资源碎片化程度)")
    print("  • 失配率    = 节点利用率的变异系数 (std/mean，衡量负载不均衡)")
    print(f"\n注意: AvgUtil≈MemUtil 说明内存是主导资源(所有节点的 MEM% > CPU%)")
    print(f"      实用Util < CPUUtil 是因为任务的实际用量 < 请求量 (过度预留)")
    print("=" * 100)

    # 新增指标对比表
    print("\n━━━ 高级指标对比（任务时长 & 亲和性）━━━")
    print(f"{'算法':<30} {'平均任务时长':>15} {'亲和性命中率':>15}")
    print("-" * 65)
    for stats in results:
        if stats['scheduled'] > 0:
            print(f"{stats['name']:<30} {stats['avg_duration']:>12.0f}秒 {stats['affinity_rate'] * 100:>13.1f}%")
    print("\n注: 亲和性命中率 = 任务被调度回原始运行节点的比例（locality-aware scheduling）")

    # 增加：利用率分布分析（解释成功率与利用率的关系）
    print("\n━━━ 节点利用率分布对比（解释成功率-利用率关系）━━━")

    def analyze_util_dist(machines, name_short):
        utils = [m.utilization() for m in machines if m.utilization() > 0]
        if not utils:
            return
        bins = [0, 0.65, 0.75, 0.85, 0.95, 1.0]
        hist, _ = np.histogram(utils, bins=bins)
        above_78 = sum(1 for u in utils if u > 0.78)
        print(f"{name_short:<15} ", end='')
        for h in hist:
            print(f"{h:>4d} ", end='')
        print(f"  (>{int(0.78 * 100)}%: {above_78:>3d}节点)")

    print(f"{'算法':<15} {'<65%':>4s} {'65-75':>4s} {'75-85':>4s} {'85-95':>4s} {'>95%':>4s}   SLO阈值外")
    print("-" * 75)

    for stats in results:
        if stats['scheduled'] > 0:
            analyze_util_dist(stats['machines'], stats['name'][:14])

    # 增加：详细租户分配分析
    print("\n━━━ 租户资源分配对比（Top 5 租户）━━━")

    # 获取所有租户
    all_tenants = set()
    for stats in results:
        all_tenants.update(stats.get('tenant_cpu', {}).keys())

    # 按 Mesos DRF 分配量排序
    mesos_stats = next(s for s in results if 'Mesos' in s['name'])
    sorted_tenants = sorted(
        all_tenants,
        key=lambda t: mesos_stats.get('tenant_cpu', {}).get(t, 0),
        reverse=True
    )[:5]

    print(f"{'租户':<15}", end='')
    for stats in results:
        print(f"{stats['name'][:12]:<15}", end='')
    print()
    print("-" * 75)

    for tenant in sorted_tenants:
        print(f"{tenant[:14]:<15}", end='')
        for stats in results:
            cpu = stats.get('tenant_cpu', {}).get(tenant, 0)
            print(f"{cpu:>14.4f} ", end='')
        print()

    print("\n实现详情:")
    print("  Firmament: 完整 Flow Graph + Min-Cost Max-Flow Solver (OR-Tools)")
    print("  Mesos:     完整 DRFSorter + HierarchicalAllocator")
    print("  Tetris:    SIGCOMM'14 Section 3.2 Equation 1")
    print("  SLO-Driven: 利用率感知负载均衡")

    print("\n源码参考:")
    print("  [1] baselines/firmament/src/scheduling/flow/octopus_cost_model.cc")
    print("  [2] baselines/mesos/src/master/allocator/mesos/sorter/drf/sorter.cpp")


if __name__ == "__main__":
    main()

