#!/usr/bin/env python3
"""
过滤时间集中的任务

从 Alibaba trace 中选择在一个短时间窗口内到达的任务，
以提高利用率测试的准确性。
"""
import sys
import os

# 添加项目根目录到路径
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT_DIR)

from tools.run_complete_comparison import load_alibaba_trace
import numpy as np

def filter_concentrated_tasks(tasks, time_window_seconds=3600):
    """
    选择在一个时间窗口内集中到达的任务
    
    Args:
        tasks: 任务列表
        time_window_seconds: 时间窗口大小（秒），默认 1 小时
    
    Returns:
        过滤后的任务列表
    """
    if not tasks:
        return []
    
    # 按到达时间排序
    sorted_tasks = sorted(tasks, key=lambda t: t.arrival)
    
    # 找到任务密度最高的时间窗口
    best_window_start = 0
    best_window_count = 0
    best_window_tasks = []
    
    for i, task in enumerate(sorted_tasks):
        window_start = task.arrival
        window_end = window_start + time_window_seconds
        
        # 统计这个窗口内的任务数
        window_tasks = [t for t in sorted_tasks[i:] 
                       if t.arrival >= window_start and t.arrival < window_end]
        
        if len(window_tasks) > best_window_count:
            best_window_count = len(window_tasks)
            best_window_start = window_start
            best_window_tasks = window_tasks
    
    print(f"\n找到最佳时间窗口:")
    print(f"  窗口大小: {time_window_seconds} 秒 ({time_window_seconds/3600:.1f} 小时)")
    print(f"  窗口开始: {best_window_start}")
    print(f"  窗口结束: {best_window_start + time_window_seconds}")
    print(f"  窗口内任务数: {best_window_count}")
    print(f"  任务密度: {best_window_count / (time_window_seconds/60):.1f} 个/分钟")
    print()
    
    return best_window_tasks

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python filter_concentrated_tasks.py /path/to/trace [max_tasks] [window_hours]")
        print()
        print("示例:")
        print("  python filter_concentrated_tasks.py ./data 10000 1")
        print("  # 加载 10000 个任务，选择 1 小时窗口内最集中的")
        sys.exit(1)
    
    trace_dir = sys.argv[1]
    max_tasks = int(sys.argv[2]) if len(sys.argv) > 2 else 10000
    window_hours = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    
    print(f"加载任务: {trace_dir}")
    print(f"最大任务数: {max_tasks}")
    print(f"目标时间窗口: {window_hours} 小时")
    print()
    
    # 加载任务
    all_tasks = load_alibaba_trace(trace_dir, max_tasks)
    
    # 过滤集中的任务
    window_seconds = int(window_hours * 3600)
    filtered_tasks = filter_concentrated_tasks(all_tasks, window_seconds)
    
    # 统计信息
    arrivals = [t.arrival for t in filtered_tasks]
    durations = [t.duration for t in filtered_tasks if t.duration > 0]
    cpus = [t.cpu for t in filtered_tasks]
    mems = [t.mem for t in filtered_tasks]
    
    print("过滤后的任务统计:")
    print(f"  任务数: {len(filtered_tasks)}")
    print(f"  CPU 需求: 平均={np.mean(cpus):.3f}, 总计={sum(cpus):.1f}")
    print(f"  MEM 需求: 平均={np.mean(mems):.3f}, 总计={sum(mems):.1f}")
    print(f"  任务时长: 平均={np.mean(durations):.0f}秒, 中位数={np.median(durations):.0f}秒")
    print(f"  到达跨度: {min(arrivals)} ~ {max(arrivals)} ({max(arrivals)-min(arrivals)} 秒)")
    print()
    
    # 理论利用率估算
    total_cpu = sum(cpus)
    total_mem = sum(mems)
    avg_duration = np.mean(durations)
    time_span = max(arrivals) - min(arrivals) + avg_duration
    
    for num_nodes in [3, 5, 10]:
        capacity = num_nodes * 11
        # 假设任务均匀分布在时间窗口内
        avg_concurrent = len(filtered_tasks) * avg_duration / time_span
        peak_util = min(total_cpu / capacity, 1.0)
        avg_util = avg_concurrent * np.mean(cpus) / capacity
        
        print(f"节点数={num_nodes}:")
        print(f"  容量: {capacity} core")
        print(f"  峰值利用率: {peak_util*100:.1f}%")
        print(f"  估算平均利用率: {avg_util*100:.1f}%")
        print()
    
    print("建议配置:")
    if avg_util * 100 < 30:
        print(f"  ⚠️  利用率可能还是偏低（~{avg_util*100:.1f}%）")
        print(f"  建议: 减少节点数或增加时间窗口")
    elif avg_util * 100 > 80:
        print(f"  ⚠️  利用率可能过高（~{avg_util*100:.1f}%），任务会排队")
        print(f"  建议: 增加节点数")
    else:
        print(f"  ✅ 利用率合理（~{avg_util*100:.1f}%）")
        print(f"  可以运行对比实验")
