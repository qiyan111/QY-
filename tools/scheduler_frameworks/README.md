# Scheduler Frameworks - 完整源码实现

本目录包含基于官方开源代码的完整调度器实现，用于严格的学术对比。

## 实现列表

| 调度器 | 源码来源 | 文件 |
|--------|---------|------|
| **Firmament** | baselines/firmament (OSDI'16) | firmament_scheduler.py |
| **Mesos DRF** | baselines/mesos (NSDI'11) | mesos_drf_allocator.py |
| **Tetris** | SIGCOMM'14 论文 | （集成在主脚本） |
| **SLO-Driven** | 本研究 | （集成在主脚本） |

## 安装依赖

```bash
cd tools/scheduler_frameworks
pip install -r requirements.txt
```

## 运行对比

```bash
cd ~/AIGC/newproject/资源分配
python tools/run_complete_comparison.py ./data
```

## 源码对应关系

### Firmament
- `flow_graph.py` ← `baselines/firmament/src/scheduling/flow/flow_graph.{cc,h}`
- `octopus_cost_model.py` ← `baselines/firmament/src/scheduling/flow/octopus_cost_model.cc`
- `min_cost_flow_solver.py` ← 使用 Google OR-Tools 替代 cs2/Relax IV

### Mesos
- `mesos_drf_allocator.py` ← `baselines/mesos/src/master/allocator/mesos/hierarchical.cpp`
- DRFSorter ← `baselines/mesos/src/master/allocator/mesos/sorter/drf/sorter.cpp`

所有实现严格按照源码逻辑，未做简化。

