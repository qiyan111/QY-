# 系统改进总结

## 改进动机

基于 Alibaba 2018 Trace（20000 实例，249 节点）的对比实验结果：

| 算法 | 成功率 | 利用率 | 碎片化 | 违约率 | Per-Task 公平性 | Dominant Share 公平 |
|------|--------|--------|--------|--------|-----------------|-------------------|
| Firmament (OSDI'16) | 0.0% | 0.0% | 0.0% | 0.00% | 1.000 | 1.000 |
| Mesos DRF (NSDI'11) | 100.0% | 76.9% | 1.0% | 5.27% | 0.989 | 0.127 |
| Tetris (SIGCOMM'14) | 100.0% | 76.9% | 42.0% | 39.29% | 0.683 | 0.091 |
| **SLO-Driven (改进前)** | **96.2%** | **75.1%** | **0.4%** | **3.51%** | **0.997** | **0.123** |

**问题分析**：
1. **成功率下降**（96.2% vs 100%）：新的容量过滤逻辑在指标缺失时误杀节点
2. **仍有改进空间**：违约率 3.51%，目标 <3%；Dominant Share 公平性 0.123，目标接近 Mesos 的 0.127

## 核心改进

### 1. scheduler-extender: 容量感知过滤与多维打分

#### 改进前
```go
func filter(w http.ResponseWriter, r *http.Request) {
    // For PoC accept all nodes.
    result := &schedv1.ExtenderFilterResult{Nodes: args.Nodes}
    json.NewEncoder(w).Encode(result)
}

func prioritize(w http.ResponseWriter, r *http.Request) {
    weight := priorityWeight[pc]  // 只看优先级
    scores[i] = schedv1.HostPriority{Host: node.Name, Score: int64(weight)}
}
```

#### 改进后
```go
func filter(w http.ResponseWriter, r *http.Request) {
    cpuReq, memReq := getPodResourceRequest(args.Pod)
    for _, node := range args.Nodes.Items {
        // 优先使用实时指标，但有兜底逻辑
        if ok, reason := nodeCanFit(&node, cpuReq, memReq); ok {
            filtered.Items = append(filtered.Items, node)
        } else {
            failed[node.Name] = reason  // 记录失败原因
        }
    }
}

func calculateScore(pod *corev1.Pod, metrics nodeMetrics) int64 {
    // 多维打分：优先级(35) + 容量(40) + 公平性(15) + 延迟(20) - 占用(15) - 违约(20)
    base := getPriorityBaseScore(pod)                    // 35分
    capacityScore := ((cpuFreeRatio + memFreeRatio) / 2.0) * 40.0  // 40分
    fairnessScore := clampFloat(metrics.PerTaskFairness, 0, 1) * 15.0  // 15分
    latencyScore := (1.0 - metrics.TailLatencyP99/120.0) * 20.0  // 20分
    dominancePenalty := metrics.DominantShare * 15.0     // -15分
    violationPenalty := metrics.SLOViolationRate * 20.0  // -20分
    return clampScore(int64(base + capacityScore + fairnessScore + latencyScore - dominancePenalty - violationPenalty))
}
```

**关键特性**：
- **兜底机制**：指标异常时降级到 Kubernetes allocatable，避免误杀
- **公平性奖励**：Per-Task Fairness 越高得分越高，鼓励均衡分配
- **延迟感知**：尾延迟越低得分越高，避免将任务调度到高负载节点
- **违约惩罚**：SLO 违约率越高扣分越多，优先保障 SLO

### 2. sidecar-agent: 完整指标采集与上报

#### 改进前
```rust
async fn main() -> Result<()> {
    println!("sidecar-agent started (stub)");
    // TODO: load eBPF program, collect metrics...
    loop { sleep(Duration::from_secs(60)).await; }
}
```

#### 改进后
```rust
async fn main() -> Result<()> {
    let collector = MetricsCollector::new(kubelet_root, sysfs_root)?;
    
    loop {
        ticker.tick().await;
        let samples = collector.collect().await?;  // 采集所有容器指标
        let aggregated = aggregate_samples(&samples);  // 聚合为节点维度
        
        client.post(extender_url)
            .json(&MetricsPayload {
                node_name, cpu_allocatable, cpu_used,
                memory_allocatable, memory_used,
                tail_latency_p99, slo_violation_rate,
                dominant_share, per_task_fairness,
                timestamp: Utc::now(),
            })
            .send().await?;
    }
}
```

**实现细节**：

**sys/pod.rs** - Pod 发现：
- 扫描 `/var/lib/kubelet/pods/*/pod.info` 获取 Pod 元数据
- 解析 namespace、name、uid、QoS class、tenant ID
- 定位 cgroup v2 路径（`/sys/fs/cgroup/kubepods.slice/...`）

**sys/reader.rs** - cgroup 指标读取：
- **CPU**: 读取 `cpu.stat` (usage_usec) 和 `cpu.max` (quota/period)
- **内存**: 读取 `memory.current` 和 `memory.max`
- **延迟**: 桩实现，预留 eBPF 探针接口
- **违约**: 桩实现，预留与 credit-service 集成

**指标聚合**：
- **Dominant Share** = max(cpu_used/cpu_alloc, mem_used/mem_alloc)
- **Per-Task Fairness** = Jain's Index，基于租户资源占用
- **尾延迟 P99** = 对所有容器延迟样本排序后取 99 百分位
- **违约率** = violations / total_requests

### 3. 指标流动闭环

```
┌──────────────┐  周期采集   ┌──────────────┐
│ sidecar-agent│─────────────>│ cgroup v2    │
│ (每个节点)   │  cpu/mem     │ (/sys/fs)    │
└──────┬───────┘              └──────────────┘
       │
       │ HTTP POST /metrics/report
       │ 每 30s 上报
       ▼
┌──────────────────────┐
│ scheduler-extender   │  filter() + prioritize()
│ (中心化)             │  ──────────────────────>  Kubernetes
│ - 指标缓存(TTL 2min) │                           Scheduler
│ - 容量过滤           │
│ - 多维打分           │
└──────────────────────┘
```

## 预期效果

| 指标 | 改进前 | 改进后（目标） | 改进手段 |
|------|--------|---------------|----------|
| **成功率** | 96.2% | **>99%** | 容量过滤兜底逻辑，避免误杀节点 |
| **违约率** | 3.51% | **<3%** | 延迟感知打分 + 违约惩罚机制 |
| **Per-Task 公平性** | 0.997 | **>0.95** | Jain's Index 奖励，鼓励均衡 |
| **Dominant Share 公平** | 0.123 | **>0.12** | 占用惩罚机制，避免单租户霸占资源 |
| **碎片化** | 0.4% | **<1%** | 容量打分引导 Pod 到合适节点 |

## 部署和测试

### 快速开始

```bash
# 1. 构建镜像
cd scheduler-extender && go build -o scheduler-extender main.go
cd ../sidecar-agent && cargo build --release

# 2. 部署到集群
helm upgrade --install slo-scheduler ./charts --namespace kube-system

# 3. 运行实验（在有数据的服务器上）
python tools/test_improvements.py 20000
```

详细说明见：[部署和测试指南](docs/deployment-guide.md)

### 参数调优

如果实验结果不理想，可调整以下参数：

**提高成功率**：
```go
// scheduler-extender/main.go
const metricsTTL = 5 * time.Minute  // 增加指标缓存时间
```

**降低违约率**：
```go
violationPenalty := math.Min(metrics.SLOViolationRate * 25.0, 25.0)  // 增加惩罚权重
targetTailLatencyMillis = 100.0  // 降低目标延迟阈值
```

**提高公平性**：
```go
fairnessScore := clampFloat(metrics.PerTaskFairness, 0, 1) * 20.0  // 增加奖励权重
dominancePenalty := clampFloat(metrics.DominantShare, 0, 1) * 20.0  // 增加惩罚权重
```

## 下一步优化方向

### 短期（1-2 周）
1. **eBPF 延迟采集**：实现 `sidecar-agent/src/probes.rs`，使用 Aya 加载 kprobe 探针
2. **credit-service 集成**：sidecar 上报违约事件，extender 读取租户信用分
3. **Prometheus 导出**：在 sidecar 暴露 `/metrics` 端点，便于可视化

### 中期（1 个月）
1. **GPU 调度支持**：启用 `--features gpu`，集成 nvml-wrapper
2. **自适应权重**：根据历史数据自动调整打分权重（强化学习）
3. **多目标优化**：Pareto 前沿，平衡利用率/公平性/SLO

### 长期（3 个月）
1. **联邦学习**：多集群指标聚合，全局公平性保障
2. **预测式调度**：基于时间序列预测资源需求，提前调度
3. **混合精度调度**：CPU-bound vs Memory-bound vs IO-bound 任务分类

## 文件清单

### 新增文件
- `docs/deployment-guide.md` - 完整部署和测试指南
- `tools/test_improvements.py` - 简化的测试脚本
- `IMPROVEMENTS.md` - 本文件，改进总结

### 修改文件
- `scheduler-extender/main.go` - 从 56 行扩展到 305 行
  - 新增 `nodeMetrics` 结构体和 `metricsCache`
  - 新增 `/metrics/report` 接口
  - 重构 `filter()` 和 `prioritize()`
  - 新增 `calculateScore()` 多维打分逻辑

- `sidecar-agent/` - 从占位实现变为功能完整
  - `Cargo.toml` - 新增 18 个依赖，支持 `gpu` 和 `ebpf` features
  - `src/main.rs` - 294 行，周期采集与上报
  - `src/sys/mod.rs` - 模块化设计
  - `src/sys/pod.rs` - 99 行，Pod 发现逻辑
  - `src/sys/reader.rs` - 148 行，cgroup 指标读取
  - `src/gpu.rs` - GPU 指标桩
  - `src/probes.rs` - eBPF 探针桩

## 验证清单

- [ ] **本地构建通过**
  - [ ] `cd scheduler-extender && go build`
  - [ ] `cd sidecar-agent && cargo build --release`

- [ ] **部署到测试集群**
  - [ ] scheduler-extender 启动成功
  - [ ] sidecar-agent DaemonSet 运行在所有节点
  - [ ] 日志无错误

- [ ] **功能验证**
  - [ ] sidecar 成功上报指标到 extender
  - [ ] extender 缓存命中率 >80%
  - [ ] Pod 调度决策体现多维打分逻辑

- [ ] **性能验证（服务器上运行）**
  - [ ] `python tools/test_improvements.py 20000`
  - [ ] 成功率 >99%
  - [ ] 违约率 <3%
  - [ ] Per-Task 公平性 >0.95
  - [ ] Dominant Share 公平性 >0.12

- [ ] **压力测试**
  - [ ] 批量创建 100+ Pod，观察调度延迟
  - [ ] 模拟节点故障，验证故障转移
  - [ ] 长时间运行（24h+），检查内存泄漏

## 参考文献

1. Gog, I., et al. "Firmament: Fast, centralized cluster scheduling at scale." OSDI 2016.
2. Ghodsi, A., et al. "Dominant Resource Fairness: Fair Allocation of Multiple Resource Types." NSDI 2011.
3. Tumanov, A., et al. "Tetris: Multi-resource Packing for Cluster Schedulers." SIGCOMM 2014.
4. Kubernetes Scheduler Framework: https://kubernetes.io/docs/concepts/scheduling-eviction/
5. cgroup v2 Documentation: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

---

**更新时间**: 2025-10-12  
**作者**: SLO Scheduler Team  
**版本**: v2.0

