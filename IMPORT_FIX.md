# 导入问题修复

## 问题

```
ImportError: cannot import name 'load_tasks' from 'load_trace_final'
```

## 修复内容

### 1. 更新 filter_peak_window.py 导入

**修复前**：
```python
from load_trace_final import load_tasks
```

**修复后**：
```python
from load_trace_final import load_alibaba_trace_final
```

### 2. 更新函数调用

**修复前**：
```python
all_tasks = load_tasks('./data', max_instances=max_tasks)
```

**修复后**：
```python
all_tasks = load_alibaba_trace_final('./data', max_inst=max_tasks)
```

### 3. 扩展 Task 类定义

在 `tools/load_trace_final.py` 中添加所有必需的字段：

```python
@dataclass
class Task:
    id: int
    cpu: float
    mem: float
    tenant: str
    arrival: int
    # 新增字段
    slo_sensitive: str = 'low'
    priority: int = 1
    start_time: int = 0
    end_time: int = 0
    cpu_avg: float = 0.0
    cpu_max: float = 0.0
    machine_id: str = ""
    duration: int = 60  # 默认60秒
    real_cpu: float = 0.0
    real_mem: float = 0.0
    mem_bandwidth: float = 0.0
    net_in: float = 0.0
    net_out: float = 0.0
```

### 4. 改进任务创建逻辑

添加 duration 计算：

```python
# 计算时长（从 start_time 和 end_time）
start_time = int(row[5]) if pd.notna(row[5]) else 0
end_time = int(row[6]) if pd.notna(row[6]) else start_time + 60
duration = max(10, end_time - start_time) if end_time > start_time else 60

task = Task(
    id=idx,
    cpu=row['cpu'],
    mem=row['mem'],
    tenant=str(row[2]),
    arrival=start_time,
    duration=duration,
    start_time=start_time,
    end_time=end_time,
    cpu_avg=row['cpu'],
    real_cpu=row['cpu'] * 0.5,  # 假设真实使用是请求的50%
    real_mem=row['mem'] * 0.5,
    machine_id=str(row[7]) if pd.notna(row[7]) else "",
)
```

## 现在可以运行了

```bash
python tools/filter_peak_window.py 4 500000
```

如果仍然遇到问题，请检查：
1. 是否在正确的目录（包含 `data` 文件夹）
2. pandas 是否已安装：`pip install pandas`
3. numpy 是否已安装：`pip install numpy`

---

**最后更新**: 2025-10-22  
**状态**: ✅ 已修复
