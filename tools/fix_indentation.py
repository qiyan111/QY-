#!/usr/bin/env python3
"""
修复 run_complete_comparison.py 中的缩进错误
在远程服务器上运行: python tools/fix_indentation.py
"""

import re

# 读取文件
with open('tools/run_complete_comparison.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 检查是否需要修复
if '            for t in batch_tasks' in content:
    print("检测到缩进错误，正在修复...")
    
    # 备份
    with open('tools/run_complete_comparison.py.bak', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✓ 已备份到 run_complete_comparison.py.bak")
    
    # 修复 firmament_schedule_batch
    content = content.replace(
        '''    firm_tasks = [
        FirmTask(id=t.id, cpu=t.cpu, mem=t.mem, tenant=t.tenant, arrival=t.arrival)
            for t in batch_tasks
        ]
        return scheduler.schedule(firm_tasks)''',
        '''        firm_tasks = [
            FirmTask(id=t.id, cpu=t.cpu, mem=t.mem, tenant=t.tenant, arrival=t.arrival)
            for t in batch_tasks
        ]
        return scheduler.schedule(firm_tasks)'''
    )
    
    # 修复 mesos_schedule_batch
    content = content.replace(
        '''    # 按租户分组任务
    tasks_by_fw = defaultdict(list)
        for task in batch_tasks:
        mesos_task = MesosTask(''',
        '''        # 按租户分组任务
        tasks_by_fw = defaultdict(list)
        for task in batch_tasks:
            mesos_task = MesosTask('''
    )
    
    content = content.replace(
        '''    # 调用 allocator
    return allocator.allocate(tasks_by_fw)''',
        '''        # 调用 allocator
        return allocator.allocate(tasks_by_fw)'''
    )
    
    # 写回文件
    with open('tools/run_complete_comparison.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✓ 缩进错误已修复")
    print("\n请重新运行: python tools/run_complete_comparison.py ./data 20000 80")
else:
    print("未检测到缩进错误，文件可能已经修复")

