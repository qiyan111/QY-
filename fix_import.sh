#!/bin/bash
# 自动修复 filter_peak_window.py 的导入问题

echo "🔧 修复 filter_peak_window.py..."

# 备份原文件
cp tools/filter_peak_window.py tools/filter_peak_window.py.bak

# 修改导入语句
sed -i 's/from load_trace_final import load_tasks/from load_trace_final import load_alibaba_trace_final/g' tools/filter_peak_window.py

# 修改函数调用
sed -i "s/all_tasks = load_tasks('./data', max_instances=max_tasks)/all_tasks = load_alibaba_trace_final('.\/data', max_inst=max_tasks)/g" tools/filter_peak_window.py

# 验证修改
echo ""
echo "✅ 验证修改:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
grep -n "import load_" tools/filter_peak_window.py
echo ""
grep -n "load_alibaba_trace_final" tools/filter_peak_window.py
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🎉 修复完成！备份保存在 tools/filter_peak_window.py.bak"
echo ""
echo "现在可以运行:"
echo "  python tools/filter_peak_window.py 4 500000"
