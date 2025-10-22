#!/usr/bin/env python3
"""
最小可运行验证：Firmament 调度器在小规模合成任务上的放置结果。
不依赖外部数据集。
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

# 确保可以通过包方式导入
TOOLS_DIR = Path(__file__).parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from scheduler_frameworks.firmament_scheduler import FirmamentScheduler, Machine, Task


def main() -> int:
    machines = [
        Machine(id=0, cpu=11.0, mem=11.0),
        Machine(id=1, cpu=11.0, mem=11.0),
        Machine(id=2, cpu=11.0, mem=11.0),
    ]
    scheduler = FirmamentScheduler(machines)

    tasks = [
        Task(id=1, cpu=1.0, mem=1.0, tenant="a", arrival=0),
        Task(id=2, cpu=2.0, mem=1.5, tenant="a", arrival=0),
        Task(id=3, cpu=3.0, mem=3.0, tenant="b", arrival=0),
        Task(id=4, cpu=0.5, mem=0.5, tenant="b", arrival=0),
        Task(id=5, cpu=4.0, mem=4.0, tenant="c", arrival=0),
    ]

    placements = scheduler.schedule(tasks)
    print("Placements (task_id -> machine_id):")
    for tid, mid in placements:
        print(f"  {tid} -> {mid}")

    # 简单断言：应至少放置一个任务
    if not placements:
        print("ERROR: 未产生任何放置结果")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
