#!/usr/bin/env python3
"""
分析成功率与利用率的关系
"""
import sys
import numpy as np

def analyze_util_distribution(machines, name):
    """分析节点利用率分布"""
    utils = [m.utilization() for m in machines]
    
    print(f"\n━━━ {name} 节点利用率分布 ━━━")
    print(f"  平均: {np.mean(utils)*100:.1f}%")
    print(f"  中位数: {np.median(utils)*100:.1f}%")
    print(f"  标准差: {np.std(utils)*100:.1f}%")
    print(f"  最大: {np.max(utils)*100:.1f}%")
    
    # 直方图
    bins = [0, 0.5, 0.65, 0.75, 0.85, 0.95, 1.0]
    hist, _ = np.histogram(utils, bins=bins)
    
    print(f"\n  利用率分段:")
    print(f"    0-50%:   {hist[0]:3d} 节点 ({hist[0]/len(utils)*100:.1f}%)")
    print(f"    50-65%:  {hist[1]:3d} 节点 ({hist[1]/len(utils)*100:.1f}%)")
    print(f"    65-75%:  {hist[2]:3d} 节点 ({hist[2]/len(utils)*100:.1f}%)")
    print(f"    75-85%:  {hist[3]:3d} 节点 ({hist[3]/len(utils)*100:.1f}%)")
    print(f"    85-95%:  {hist[4]:3d} 节点 ({hist[4]/len(utils)*100:.1f}%)")
    print(f"    95-100%: {hist[5]:3d} 节点 ({hist[5]/len(utils)*100:.1f}%)")
    
    # 关键：有多少节点在 78% 以上（SLO-Driven 的阈值）
    above_78 = sum(1 for u in utils if u > 0.78)
    print(f"\n  > 78% (SLO阈值): {above_78} 节点 ({above_78/len(utils)*100:.1f}%)")

if __name__ == "__main__":
    print("此脚本需要在 run_complete_comparison.py 中调用")
    print("或者修改 run_complete_comparison.py 在返回结果后调用此分析")

