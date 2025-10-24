#!/usr/bin/env python3
"""
运行时间窗口过滤后的对比实验

使用方法:
    python tools/run_peak_window.py [num_nodes]
    
前提: 先运行 filter_peak_window.py 生成 peak_window_tasks.pkl
"""
import sys
import os
import pickle

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))

def main():
    # 加载过滤后的任务
    pkl_file = 'peak_window_tasks.pkl'
    
    if not os.path.exists(pkl_file):
        print(f"❌ 错误: 未找到 {pkl_file}")
        print(f"   请先运行: python tools/filter_peak_window.py")
        sys.exit(1)
    
    print("=" * 70)
    print("🚀 运行时间窗口过滤后的调度器对比")
    print("=" * 70)
    print()
    
    with open(pkl_file, 'rb') as f:
        data = pickle.load(f)
    
    tasks = data['tasks']
    window_start = data['window_start']
    window_size = data['window_size']
    window_hours = data['window_hours']
    stats = data['stats']
    
    print(f"📂 加载过滤后的任务:")
    print(f"  窗口大小: {window_hours} 小时")
    print(f"  窗口开始: {window_start} 秒")
    print(f"  任务数: {len(tasks)}")
    print(f"  理论并发: {stats['theoretical_concurrent']:.1f} cores")
    print()
    
    # 获取节点数
    if len(sys.argv) > 1:
        num_nodes = int(sys.argv[1])
    else:
        num_nodes = stats['recommended_nodes']
    
    print(f"⚙️ 配置:")
    print(f"  节点数: {num_nodes} ({num_nodes * 11.0:.0f} cores)")
    print(f"  调度间隔: {os.getenv('BATCH_STEP_SECONDS', '未设置 (使用默认值)')}")
    print()
    
    # 导入并运行对比
    from run_complete_comparison import (
        run_mesos_drf, run_tetris, run_nextgen_scheduler,
        analyze_result
    )
    
    print("━" * 70)
    print("开始运行调度器对比...")
    print("━" * 70)
    
    results = []
    
    # 运行 Mesos DRF
    try:
        res_mesos = run_mesos_drf(tasks, num_nodes)
        results.append(analyze_result(res_mesos, './data', tasks))
    except Exception as e:
        print(f"❌ Mesos DRF 失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 运行 Tetris
    try:
        res_tetris = run_tetris(tasks, num_nodes)
        results.append(analyze_result(res_tetris, './data', tasks))
    except Exception as e:
        print(f"❌ Tetris 失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 运行 NextGen
    try:
        res_nextgen = run_nextgen_scheduler(tasks, num_nodes)
        results.append(analyze_result(res_nextgen, './data', tasks))
    except Exception as e:
        print(f"❌ NextGen 失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 打印结果
    if results:
        print("\n" + "=" * 105)
        print(f"时间窗口过滤对比 (Alibaba Trace, {len(tasks)} 任务, {num_nodes} 节点, {window_hours}h 窗口)")
        print("=" * 105)
        print(f"{'算法':<40} {'成功率':>8} {'AvgUtil':>8} {'CPUUtil':>8} {'MemUtil':>10} "
              f"{'碎片率':>8} {'实用Util':>10} {'最大Util':>10} {'失配率':>10}")
        print("-" * 105)
        
        for r in results:
            frag = r.get('frag', 1.0 - r.get('avg_util', 0.0))
            imbalance = r.get('imbalance', 0.0)
            print(f"{r['name']:<40} {r['success_rate']:>7.1f}% {r['avg_util']*100:>7.1f}% "
                  f"{r['cpu_util']*100:>7.1f}% {r['mem_util']*100:>9.1f}% "
                  f"{frag*100:>7.1f}% {r['effective_util']*100:>9.1f}% "
                  f"{r['max_util']*100:>9.1f}% {imbalance*100:>9.1f}%")
        
        print("\n" + "=" * 105)
        
        # 高亮最佳结果
        print("\n🏆 最佳性能:")
        best_success = max(results, key=lambda x: x['success_rate'])
        best_util = max(results, key=lambda x: x['avg_util'])
        best_effective = max(results, key=lambda x: x['effective_util'])
        
        print(f"  • 最高成功率: {best_success['name']} ({best_success['success_rate']:.1f}%)")
        print(f"  • 最高利用率: {best_util['name']} ({best_util['avg_util']*100:.1f}%)")
        print(f"  • 最高实用率: {best_effective['name']} ({best_effective['effective_util']*100:.1f}%)")
        print()


if __name__ == '__main__':
    main()
