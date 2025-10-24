#!/usr/bin/env python3
"""
快速修复：为基线算法添加重试机制和更合理的调度间隔

用法:
    export BATCH_STEP_SECONDS=10      # 减小调度间隔（默认60秒 → 10秒）
    export ENABLE_TASK_RETRY=1        # 启用任务重试
    python tools/run_complete_comparison.py ./data 10000
"""
import os

# 推荐配置
print("=" * 80)
print("基线算法调优建议")
print("=" * 80)
print()
print("问题诊断:")
print("  • Mesos DRF 利用率 5.4% → 可能调度间隔过大，资源释放后未及时重新调度")
print("  • Tetris 成功率 18.7% → 可能缺少重试机制，失败任务直接丢弃")
print()
print("推荐修复:")
print("  1. 减小调度间隔（60秒 → 10秒）")
print("  2. 启用任务重试（最多3次）")
print("  3. 动态调整节点数（确保有足够容量）")
print()
print("快速修复命令:")
print()
print("  # 方案A：减小调度间隔 + 启用重试")
print("  export BATCH_STEP_SECONDS=10")
print("  export ENABLE_TASK_RETRY=1")
print("  python tools/run_complete_comparison.py ./data 100000 80")
print()
print("  # 方案B：使用更激进的配置")
print("  export BATCH_STEP_SECONDS=5")
print("  export ENABLE_TASK_RETRY=1")
print("  export TARGET_UTIL=0.9  # 允许更高利用率")
print("  python tools/run_complete_comparison.py ./data 100000")
print()
print("  # 方案C：只运行 NextGen（对比基准）")
print("  export ENABLE_FIRMAMENT=0")
print("  export ENABLE_SLO_DRIVEN=0")
print("  python tools/run_complete_comparison.py ./data 100000 80")
print()
print("=" * 80)
print()
print("预期改进:")
print("  Mesos DRF:  成功率 92.7% → 99%+,  利用率 5.4% → 75%+")
print("  Tetris:     成功率 18.7% → 95%+,  利用率 1.1% → 70%+")
print()
print("如果修复后仍有问题，请运行:")
print("  export DEBUG_EVENT_LOOP=1")
print("  python tools/run_complete_comparison.py ./data 1000 10  # 小规模测试")
print()
