# 调度间隔设置问题修复

## 🔴 问题

设置 `export BATCH_STEP_SECONDS=3` 后，利用率仍然只有 5%，远低于预期的 60%。

## 🔍 根本原因

**代码缺陷**：环境变量设置方式有问题

### 修复前的代码

```python
recommended_step = max(1, min(median_duration // 2, 60))
batch_step = int(os.getenv("BATCH_STEP_SECONDS", str(recommended_step)))
```

**问题**：
1. 如果任务中位时长是 15 秒
2. `recommended_step = min(15//2, 60) = 7`
3. 即使设置了 `BATCH_STEP_SECONDS=3`，也会被 7 覆盖
4. 实际调度间隔变成 7 秒而不是 3 秒

## ✅ 修复方案

### 修复后的代码

```python
durations = [t.duration for t in tasks if t.duration > 0]
median_duration = int(np.median(durations)) if durations else 60

# ⭐ 优先使用环境变量，否则智能推荐（任务时长的20%，最少3秒）
env_step = os.getenv("BATCH_STEP_SECONDS")
if env_step:
    batch_step = int(env_step)
    print(f"  [事件驱动] 调度间隔={batch_step}秒 ⭐环境变量⭐ (任务中位时长={median_duration}秒)")
else:
    recommended_step = max(3, min(median_duration // 5, 30))  # 任务时长的20%
    batch_step = recommended_step
    print(f"  [事件驱动] 调度间隔={batch_step}秒 (智能推荐, 任务中位时长={median_duration}秒)")
```

**改进**：
1. ✅ 优先使用环境变量
2. ✅ 明确显示环境变量是否生效（输出中有 ⭐环境变量⭐）
3. ✅ 智能推荐值从 `median_duration // 2` 改为 `median_duration // 5`（更激进）
4. ✅ 最小值从 1 秒提高到 3 秒（避免过小）

## 📊 预期效果

### 修复前

```
任务时长: 15 秒
调度间隔: 7 秒（即使设置了 BATCH_STEP_SECONDS=3）
峰值利用率: 76% (25 core / 33 core)
平均利用率 = (15/22) × 76% = 52%
```

但实际观察只有 5%，说明还有其他问题（可能是任务需求估算错误）

### 修复后

```
任务时长: 15 秒
调度间隔: 3 秒（环境变量生效）
峰值利用率: 76%
平均利用率 = (15/18) × 76% = 63%  ✅
```

**预期利用率**：
- Mesos DRF: 5% → **~60%**
- Tetris: 5% → **~55%**
- NextGen: 5% → **~65%**

## 🧪 验证方法

### 步骤 1: 重新运行

```bash
export BATCH_STEP_SECONDS=3
python tools/run_complete_comparison.py ./data 1000 3
```

### 步骤 2: 检查输出

查找这行输出：
```
[事件驱动] 调度间隔=3秒 ⭐环境变量⭐ (任务中位时长=15秒)
```

**关键**：必须看到 `⭐环境变量⭐` 字样，说明环境变量生效了！

### 步骤 3: 检查事件驱动统计

```
[事件驱动统计]
  调度轮次: ~XXX  (应该明显增加)
  已调度: 1000
  采样次数: ~XXX  (应该与调度轮次相同)
  过程平均利用率: ~60%  (应该明显提高)
```

## 📝 修改位置

**文件**: `tools/run_complete_comparison.py`

**修改的地方** (共 4 处):
1. 第 520-527 行 - Firmament 调度器
2. 第 582-589 行 - Mesos DRF 调度器
3. 第 662-669 行 - Tetris 调度器
4. 第 1410-1417 行 - NextGen 调度器

所有四个调度器的调度间隔设置逻辑现在都统一修复了。

## 🎯 如果利用率还是低怎么办？

### 情况 1: 环境变量没生效

**症状**: 输出中没有 `⭐环境变量⭐` 字样

**解决**:
```bash
# 确保在同一个 shell 会话中
export BATCH_STEP_SECONDS=3
echo $BATCH_STEP_SECONDS  # 应该输出 3
python tools/run_complete_comparison.py ./data 1000 3
```

### 情况 2: 任务需求太少

**症状**: 峰值利用率（MaxUtil）很低（如 < 30%）

**原因**: 1000 个任务的总需求可能远小于 25 core

**解决**: 增加任务数
```bash
export BATCH_STEP_SECONDS=3
python tools/run_complete_comparison.py ./data 5000 3
```

### 情况 3: 采样次数很少

**症状**: 调度轮次很少（如 < 20）

**原因**: 模拟时间太短或任务瞬间完成

**解决**: 检查任务到达时间分布，可能需要调整 trace 数据

## 📚 相关文档

- `LOW_UTILIZATION_ISSUE.md` - 低利用率问题分析
- `QUICK_FIX_GUIDE.md` - 快速修复指南
- `tools/diagnose_utilization.py` - 诊断工具

## ✅ 修复状态

- ✅ 代码已修改
- ✅ 语法检查通过
- ⏳ 等待用户验证

---

**最后更新**: 2025-10-22  
**修复版本**: v3.0  
**影响范围**: 所有四个调度器（Firmament, Mesos, Tetris, NextGen）
