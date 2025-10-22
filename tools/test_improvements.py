#!/usr/bin/env python3
"""
测试改进效果的脚本
在具备 Alibaba 2018 Trace 数据的服务器上运行
"""

import sys
import os
import subprocess
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'scheduler_frameworks'))

def check_environment():
    """检查运行环境"""
    print("━━━ 环境检查 ━━━")
    
    # 检查数据文件
    data_path = Path("./data/batch_instance.csv")
    if not data_path.exists():
        print(f"❌ 数据文件不存在: {data_path}")
        print("   请确保在服务器上运行此脚本")
        return False
    
    file_size_gb = data_path.stat().st_size / (1024**3)
    print(f"✓ 数据文件存在: {data_path} ({file_size_gb:.1f} GB)")
    
    # 检查 Python 依赖
    try:
        import numpy as np
        import ortools
        print(f"✓ numpy: {np.__version__}")
        print(f"✓ ortools 已安装")
    except ImportError as e:
        print(f"❌ 缺少依赖: {e}")
        print("   运行: pip install -r tools/scheduler_frameworks/requirements.txt")
        return False
    
    return True


def run_baseline_comparison(num_instances=20000):
    """运行基线对比实验"""
    print(f"\n━━━ 运行完整对比 ({num_instances} 实例) ━━━\n")
    
    cmd = [
        sys.executable,
        "tools/run_complete_comparison.py",
        "./data",
        str(num_instances)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr, file=sys.stderr)
    
    return result.returncode == 0


def analyze_results():
    """分析结果并给出改进建议"""
    print("\n━━━ 结果分析 ━━━\n")
    
    print("关键指标对比:")
    print("┌─────────────────────┬──────────┬──────────┬────────┐")
    print("│ 指标                │ 改进前   │ 改进后   │ 目标   │")
    print("├─────────────────────┼──────────┼──────────┼────────┤")
    print("│ 成功率              │ 96.2%    │ ?        │ >99%   │")
    print("│ 利用率              │ 75.1%    │ ?        │ >76%   │")
    print("│ 违约率              │ 3.51%    │ ?        │ <3%    │")
    print("│ Per-Task 公平性     │ 0.997    │ ?        │ >0.95  │")
    print("│ Dominant Share 公平 │ 0.123    │ ?        │ >0.12  │")
    print("└─────────────────────┴──────────┴──────────┴────────┘")
    
    print("\n改进要点:")
    print("1. 如果成功率仍 <99%:")
    print("   → 检查 nodeCanFit 的兜底逻辑是否生效")
    print("   → 查看 scheduler-extender 日志，确认指标缓存命中率")
    print()
    print("2. 如果违约率未降低:")
    print("   → 增加 violationPenalty 权重（从 20 增加到 25）")
    print("   → 降低 targetTailLatencyMillis（从 120 到 100）")
    print()
    print("3. 如果公平性未改善:")
    print("   → 确保 Pod 带有 tenant.slo.io/id 标签")
    print("   → 增加 fairnessScore 权重（从 15 增加到 20）")
    print("   → 增加 dominancePenalty 权重（从 15 增加到 20）")
    print()
    print("4. Firmament 结果为 0%:")
    print("   → 检查 Flow Graph 构建是否正确")
    print("   → 运行: python tools/debug_firmament.py ./data")


def main():
    if len(sys.argv) < 2:
        print("用法: python test_improvements.py <num_instances>")
        print("示例: python test_improvements.py 20000")
        sys.exit(1)
    
    num_instances = int(sys.argv[1])
    
    if not check_environment():
        sys.exit(1)
    
    success = run_baseline_comparison(num_instances)
    
    analyze_results()
    
    if success:
        print("\n✓ 实验完成")
    else:
        print("\n❌ 实验失败，请检查日志")
        sys.exit(1)


if __name__ == "__main__":
    main()

