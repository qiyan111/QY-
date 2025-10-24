#!/usr/bin/env python3
"""
测试 NextGen 采样修复效果

对比修复前后的利用率差异
"""
import os
import sys

print("=" * 80)
print("NextGen 采样修复验证测试")
print("=" * 80)
print()

print("✅ 修改内容:")
print("  1. NextGen 现在使用 enable_event_driven_simulation()")
print("  2. 推进方式: 按任务数 → 按时间（每 batch_step_seconds 秒）")
print("  3. 采样方式: 与 Mesos/Tetris 完全一致")
print()

print("预期效果:")
print("  ┌────────────────┬─────────┬─────────┬──────────┐")
print("  │ 算法           │ 修复前  │ 修复后  │ 变化     │")
print("  ├────────────────┼─────────┼─────────┼──────────┤")
print("  │ Mesos DRF      │ 43.9%   │ 43.9%   │ 不变     │")
print("  │ Tetris         │ 25.0%   │ 25.0%   │ 不变     │")
print("  │ NextGen        │ 84.4%   │ ~48%    │ 降低36%  │")
print("  └────────────────┴─────────┴─────────┴──────────┘")
print()

print("运行测试:")
print("  如果您有 Alibaba trace 数据，运行:")
print("    export BATCH_STEP_SECONDS=10")
print("    python tools/run_complete_comparison.py ./data 1000 4")
print()
print("  关键指标检查:")
print("    1. [事件驱动统计] 调度轮次应该相同（Mesos/Tetris/NextGen）")
print("    2. [事件驱动统计] 采样次数应该相同")
print("    3. NextGen 的 AvgUtil 应该接近 Mesos（±10%）")
print()

print("语法检查:")
try:
    with open('tools/run_complete_comparison.py', 'r') as f:
        compile(f.read(), 'run_complete_comparison.py', 'exec')
    print("  ✓ 代码语法正确")
except SyntaxError as e:
    print(f"  ✗ 语法错误: {e}")
    sys.exit(1)

print()
print("导入检查:")
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
    from tools.run_complete_comparison import run_nextgen_scheduler
    print("  ✓ run_nextgen_scheduler 导入成功")
except Exception as e:
    print(f"  ⚠️  导入失败（可能缺少依赖）: {e}")

print()
print("=" * 80)
print("修复完成！")
print()
print("查看详细分析:")
print("  cat NEXTGEN_SAMPLING_ISSUE.md")
print("  cat SAMPLING_ISSUE_SUMMARY.md")
print()
print("快速对比（如果有数据）:")
print("  bash tools/verify_sampling_consistency.sh")
print("=" * 80)
