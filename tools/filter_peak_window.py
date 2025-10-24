#!/usr/bin/env python3
"""
找到 Alibaba Trace 中任务最密集的时间窗口

使用方法:
    python tools/filter_peak_window.py [window_hours] [max_tasks]
    
示例:
    python tools/filter_peak_window.py 4 500000
"""
import sys
import os
sys.path.insert(0, 'tools')
from load_trace_final import load_tasks
import pickle
import numpy as np

def find_peak_window(tasks, window_size_seconds):
    """找到任务最密集的时间窗口"""
    if not tasks:
        return 0, []
    
    # 按到达时间排序
    sorted_tasks = sorted(tasks, key=lambda t: t.arrival)
    
    max_count = 0
    best_start = sorted_tasks[0].arrival
    best_end = best_start + window_size_seconds
    
    print(f"  扫描 {len(sorted_tasks)} 个任务，寻找最密集的 {window_size_seconds/3600:.1f}h 窗口...")
    
    # 使用滑动窗口找到最密集的区间
    # 为了效率，我们每隔一定间隔采样
    sample_interval = max(1, len(sorted_tasks) // 1000)
    
    for i in range(0, len(sorted_tasks), sample_interval):
        start_time = sorted_tasks[i].arrival
        end_time = start_time + window_size_seconds
        
        # 计算这个窗口内的任务数
        count = sum(1 for t in sorted_tasks 
                   if start_time <= t.arrival < end_time)
        
        if count > max_count:
            max_count = count
            best_start = start_time
            best_end = end_time
    
    # 提取窗口内的任务
    filtered = [t for t in sorted_tasks 
                if best_start <= t.arrival < best_end]
    
    return best_start, filtered


def analyze_window(tasks, window_start, window_size):
    """分析窗口内的任务特征"""
    if not tasks:
        return {}
    
    # 基本统计
    total_cpu = sum(t.cpu for t in tasks)
    total_mem = sum(t.mem for t in tasks)
    avg_cpu = total_cpu / len(tasks)
    avg_mem = total_mem / len(tasks)
    
    # 时长统计
    durations = [t.duration for t in tasks if t.duration > 0]
    avg_duration = np.mean(durations) if durations else 0
    median_duration = np.median(durations) if durations else 0
    
    # 理论并发度
    # 假设任务均匀分布在窗口内
    total_work = sum(t.cpu * t.duration for t in tasks if t.duration > 0)
    theoretical_concurrent = total_work / window_size if window_size > 0 else 0
    
    # 推荐节点数（留30%缓冲）
    recommended_nodes = int(theoretical_concurrent / 11.0 * 1.3)
    recommended_nodes = max(5, recommended_nodes)  # 至少5个节点
    
    return {
        'task_count': len(tasks),
        'total_cpu': total_cpu,
        'total_mem': total_mem,
        'avg_cpu': avg_cpu,
        'avg_mem': avg_mem,
        'avg_duration': avg_duration,
        'median_duration': median_duration,
        'theoretical_concurrent': theoretical_concurrent,
        'recommended_nodes': recommended_nodes,
    }


def main():
    # 解析参数
    window_hours = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    max_tasks = int(sys.argv[2]) if len(sys.argv) > 2 else 500000
    
    window_size = int(window_hours * 3600)
    
    print("=" * 70)
    print("🔍 Alibaba Trace 时间窗口过滤工具")
    print("=" * 70)
    print(f"\n配置:")
    print(f"  窗口大小: {window_hours} 小时 ({window_size} 秒)")
    print(f"  最大加载任务数: {max_tasks}")
    print()
    
    # 加载任务
    print("📂 加载 Alibaba Trace...")
    all_tasks = load_tasks('./data', max_instances=max_tasks)
    print(f"  ✅ 加载了 {len(all_tasks)} 个任务")
    
    # 分析原始trace
    if all_tasks:
        min_arrival = min(t.arrival for t in all_tasks)
        max_arrival = max(t.arrival for t in all_tasks)
        time_span = max_arrival - min_arrival
        print(f"  时间跨度: {time_span} 秒 = {time_span/3600:.1f} 小时 = {time_span/86400:.1f} 天")
    
    # 找到最密集的窗口
    print(f"\n🔍 寻找最密集的 {window_hours} 小时窗口...")
    best_start, filtered_tasks = find_peak_window(all_tasks, window_size)
    
    if not filtered_tasks:
        print("  ❌ 未找到任务")
        return
    
    print(f"  ✅ 找到最密集窗口:")
    print(f"     开始时间: {best_start} 秒")
    print(f"     结束时间: {best_start + window_size} 秒")
    print(f"     任务数: {len(filtered_tasks)}")
    
    # 分析窗口
    print(f"\n📊 窗口特征分析:")
    stats = analyze_window(filtered_tasks, best_start, window_size)
    
    print(f"  任务统计:")
    print(f"    总任务数: {stats['task_count']}")
    print(f"    平均 CPU: {stats['avg_cpu']:.3f} cores/任务")
    print(f"    平均 MEM: {stats['avg_mem']:.3f} GB/任务")
    print(f"    平均时长: {stats['avg_duration']:.1f} 秒")
    print(f"    中位时长: {stats['median_duration']:.1f} 秒")
    
    print(f"\n  并发度分析:")
    print(f"    理论平均并发: {stats['theoretical_concurrent']:.1f} cores")
    print(f"    推荐节点数: {stats['recommended_nodes']} 节点 ({stats['recommended_nodes'] * 11.0:.0f} cores)")
    
    # 计算预期利用率
    if stats['recommended_nodes'] > 0:
        capacity = stats['recommended_nodes'] * 11.0
        expected_util = stats['theoretical_concurrent'] / capacity * 100
        print(f"    预期平均利用率: {expected_util:.1f}%")
    
    # 保存结果
    output_file = 'peak_window_tasks.pkl'
    result = {
        'tasks': filtered_tasks,
        'window_start': best_start,
        'window_size': window_size,
        'window_hours': window_hours,
        'stats': stats,
    }
    
    with open(output_file, 'wb') as f:
        pickle.dump(result, f)
    
    print(f"\n💾 已保存到: {output_file}")
    
    # 生成运行命令
    print(f"\n🚀 推荐运行命令:")
    print(f"  export BATCH_STEP_SECONDS=3")
    print(f"  python tools/run_peak_window.py {stats['recommended_nodes']}")
    print()
    print(f"  预期结果:")
    print(f"    • 成功率: 95-100%")
    print(f"    • 利用率: 50-70%")
    print(f"    • 算法差异明显")
    print(f"    • NextGen 领先 5-10%")
    print()
    
    print("=" * 70)


if __name__ == '__main__':
    main()
