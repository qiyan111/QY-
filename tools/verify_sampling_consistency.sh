#!/bin/bash
# 验证采样一致性脚本

echo "============================================================"
echo "NextGen 采样一致性验证"
echo "============================================================"
echo ""

echo "测试1: 对比 NextGen 启用/禁用动态资源释放的差异"
echo "------------------------------------------------------------"

echo ""
echo "[测试1.1] NextGen WITH 动态资源释放 (当前实现)"
export NEXTGEN_DYNAMIC_RELEASE=1
export BATCH_STEP_SECONDS=10
python3 tools/run_complete_comparison.py ./data 1000 4 2>&1 | \
    grep -A 1 "NextGen Scheduler" | grep -E "NextGen|AvgUtil|调度轮次|采样次数"

echo ""
echo "[测试1.2] NextGen WITHOUT 动态资源释放 (静态模式)"
export NEXTGEN_DYNAMIC_RELEASE=0
python3 tools/run_complete_comparison.py ./data 1000 4 2>&1 | \
    grep -A 1 "NextGen Scheduler" | grep -E "NextGen|AvgUtil|调度轮次|采样次数"

echo ""
echo "============================================================"
echo "测试2: 检查三个算法的采样统计"
echo "============================================================"
echo ""

export NEXTGEN_DYNAMIC_RELEASE=1
export BATCH_STEP_SECONDS=10
export DEBUG_EVENT_LOOP=0

python3 tools/run_complete_comparison.py ./data 1000 4 2>&1 | \
    grep -E "\[事件驱动统计\]|调度轮次|采样次数|过程平均利用率" | \
    head -20

echo ""
echo "============================================================"
echo "预期结果分析:"
echo "------------------------------------------------------------"
echo "如果采样一致，应该满足:"
echo "  1. Mesos/Tetris/NextGen 的 [调度轮次] 应该相同"
echo "  2. Mesos/Tetris/NextGen 的 [采样次数] 应该相同"
echo "  3. Mesos/NextGen 的 [平均利用率] 应该接近 (±10%)"
echo ""
echo "如果 NextGen 的利用率远高于 Mesos (>2x)，说明:"
echo "  ❌ NextGen 使用的是静态循环采样，不是事件驱动采样"
echo "  ❌ 采样方式不一致，对比结果不公平"
echo ""
echo "修复方案: 让 NextGen 也使用 enable_event_driven_simulation()"
echo "详细分析: 见 /workspace/NEXTGEN_SAMPLING_ISSUE.md"
echo "============================================================"
