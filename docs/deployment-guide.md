# 部署和测试指南

## 概述

本指南说明如何部署改进后的 SLO-Driven 调度系统，并运行针对性实验验证违约率和公平性指标的改善。

## 改进内容

### 1. scheduler-extender 增强

**容量感知过滤** (`nodeCanFit`):
- 优先使用 sidecar-agent 上报的实时指标（CPU/内存使用量）
- 当指标可疑（负值、allocatable<used）时，自动降级到 Kubernetes allocatable
- 避免因指标缺失导致的误杀节点，提高调度成功率

**高级节点打分** (`calculateScore`):
- **基础分数**(35分): 根据 Pod 优先级（high/medium/low）
- **容量分数**(40分): CPU/内存剩余比例的加权平均
- **公平性奖励**(15分): Per-Task 公平性越高得分越高
- **延迟分数**(20分): 尾延迟越低得分越高（目标 120ms）
- **占用惩罚**(-15分): Dominant Share 越高扣分越多
- **违约惩罚**(-20分): SLO 违约率越高扣分越多

**指标上报接口** (`/metrics/report`):
- 接收 sidecar-agent 周期性上报的节点指标
- 带 TTL 的内存缓存（默认 2 分钟）
- JSON 格式，包含 CPU/内存/延迟/违约率/公平性等

### 2. sidecar-agent 完整实现

**核心模块**:
- `sys/pod.rs`: 发现节点上所有 Pod，读取 kubelet pod.info 和 cgroup 路径
- `sys/reader.rs`: 读取 cgroup v2 指标（cpu.stat, cpu.max, memory.current, memory.max）
- `gpu.rs`: GPU 指标采集（可选，需要 `--features gpu`）
- `probes.rs`: eBPF 探针桩（可选，需要 `--features ebpf`）

**指标聚合**:
- **CPU/内存**: 累加所有容器的 limit 和 usage，计算节点总量
- **尾延迟 P99**: 对所有容器的延迟样本排序后取 99 百分位
- **违约率**: violations / total_requests
- **Dominant Share**: max(cpu_ratio, mem_ratio)
- **Per-Task 公平性**: Jain's Index，基于每个租户的资源占用

**环境变量配置**:
```bash
NODE_NAME=node-1                                          # 节点名称
METRICS_SCRAPE_INTERVAL_SECS=30                          # 采样间隔（秒）
EXTENDER_METRICS_URL=http://scheduler-extender:9001/metrics/report
KUBELET_ROOT=/var/lib/kubelet                            # kubelet 数据目录
SYSFS_ROOT=/sys/fs/cgroup                                # cgroup v2 根目录
```

## 部署步骤

### 前置条件

1. Kubernetes 集群（推荐 v1.27+）
2. cgroup v2 已启用（检查 `/sys/fs/cgroup/cgroup.controllers`）
3. Rust 工具链（用于构建 sidecar-agent，版本 1.70+）
4. Go 工具链（用于构建 scheduler-extender，版本 1.21+）

### 1. 构建镜像

```bash
# 构建 scheduler-extender
cd scheduler-extender
go build -o scheduler-extender main.go
docker build -t scheduler-extender:latest .

# 构建 sidecar-agent
cd ../sidecar-agent
cargo build --release
docker build -t sidecar-agent:latest .

# 推送到镜像仓库（替换为你的仓库地址）
docker tag scheduler-extender:latest your-registry/scheduler-extender:v2
docker tag sidecar-agent:latest your-registry/sidecar-agent:v2
docker push your-registry/scheduler-extender:v2
docker push your-registry/sidecar-agent:v2
```

### 2. 更新 Helm Chart

编辑 `charts/values.yaml`:
```yaml
schedulerExtender:
  image: your-registry/scheduler-extender:v2
  
sidecarAgent:
  image: your-registry/sidecar-agent:v2
  env:
    - name: NODE_NAME
      valueFrom:
        fieldRef:
          fieldPath: spec.nodeName
    - name: METRICS_SCRAPE_INTERVAL_SECS
      value: "30"
    - name: EXTENDER_METRICS_URL
      value: "http://scheduler-extender:9001/metrics/report"
```

### 3. 部署到集群

```bash
# 部署或升级
helm upgrade --install slo-scheduler ./charts \
  --namespace kube-system \
  --set schedulerExtender.image=your-registry/scheduler-extender:v2 \
  --set sidecarAgent.image=your-registry/sidecar-agent:v2

# 验证部署
kubectl get pods -n kube-system -l app=scheduler-extender
kubectl get pods -n kube-system -l app=sidecar-agent

# 查看日志
kubectl logs -n kube-system -l app=scheduler-extender --tail=100
kubectl logs -n kube-system -l app=sidecar-agent --tail=100 -c sidecar-agent
```

### 4. 配置 Kubernetes Scheduler

编辑 kube-scheduler 配置，添加 extender:
```yaml
apiVersion: kubescheduler.config.k8s.io/v1
kind: KubeSchedulerConfiguration
extenders:
  - urlPrefix: "http://scheduler-extender:9001"
    filterVerb: "filter"
    prioritizeVerb: "prioritize"
    weight: 10
    enableHTTPS: false
    nodeCacheCapable: true
```

## 运行实验

### 模拟调度测试（服务器环境）

在具备 Alibaba 2018 Trace 数据的服务器上运行：

```bash
# 1. 激活 Python 虚拟环境
cd ~/AIGC/newproject/资源分配
source .venv/bin/activate

# 2. 确认数据文件存在
ls -lh ./data/batch_instance.csv

# 3. 运行完整对比（20000 实例）
python tools/run_complete_comparison.py ./data 20000

# 4. 查看结果
# 重点关注：
# - 成功率（目标：接近 100%）
# - 违约率（目标：< 5%）
# - Per-Task 公平性（目标：> 0.95）
# - Dominant Share 公平性（目标：接近 Mesos DRF）
```

### 预期改进

| 指标 | 改进前 | 改进后（目标） | 说明 |
|------|--------|---------------|------|
| 成功率 | 96.2% | > 99% | 增强容量过滤兜底逻辑 |
| 违约率 | 5.49% → 3.51% | < 3% | 延迟感知打分 + SLO 惩罚 |
| Per-Task 公平性 | 0.895 → 0.997 | > 0.95 | Jain's Index 奖励 |
| Dominant Share 公平性 | 0.040 → 0.123 | > 0.1 | 占用惩罚机制 |

### 真实集群验证

1. **部署测试负载**:
```bash
# 创建不同优先级的测试 Pod
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: high-priority-workload
  labels:
    tenant.slo.io/id: tenant-a
spec:
  priorityClassName: high-priority
  containers:
  - name: app
    image: nginx
    resources:
      requests:
        cpu: 2
        memory: 4Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: low-priority-workload
  labels:
    tenant.slo.io/id: tenant-b
spec:
  priorityClassName: low-priority
  containers:
  - name: app
    image: nginx
    resources:
      requests:
        cpu: 1
        memory: 2Gi
EOF
```

2. **监控指标**:
```bash
# 查看调度决策日志
kubectl logs -n kube-system -l app=scheduler-extender -f | grep -E "filter|prioritize|score"

# 查看 sidecar 上报的指标
kubectl logs -n kube-system -l app=sidecar-agent -f | grep "reported metrics"

# 验证 Pod 调度到合适的节点
kubectl get pods -o wide
kubectl describe pod high-priority-workload | grep "Node:"
```

3. **压力测试**:
```bash
# 批量创建 Pod
for i in {1..100}; do
  kubectl run test-$i --image=nginx \
    --requests=cpu=100m,memory=128Mi \
    --labels=tenant.slo.io/id=tenant-test
done

# 观察调度延迟和成功率
kubectl get events --sort-by=.metadata.creationTimestamp | grep Scheduled
```

## 故障排查

### 问题 1: 成功率下降

**症状**: 调度成功率 < 98%

**原因**: sidecar 指标异常导致节点被误判为资源不足

**解决**:
1. 检查 sidecar 日志是否有采集错误
2. 验证 cgroup 路径是否正确（`/sys/fs/cgroup/kubepods.slice/...`）
3. 临时禁用实时指标，使用 K8s allocatable（修改 `nodeCanFit` 始终走降级逻辑）

### 问题 2: 指标未上报

**症状**: scheduler-extender 日志显示 "metrics not found, using base score"

**原因**: sidecar 与 extender 网络不通或采集失败

**解决**:
```bash
# 测试网络连通性
kubectl exec -n kube-system sidecar-agent-xxx -- \
  curl -v http://scheduler-extender:9001/metrics/report

# 检查 sidecar 是否成功读取 cgroup
kubectl exec -n kube-system sidecar-agent-xxx -- \
  ls -l /sys/fs/cgroup/kubepods.slice

# 手动触发上报测试
kubectl exec -n kube-system sidecar-agent-xxx -- \
  kill -USR1 1  # 如果实现了信号处理
```

### 问题 3: 公平性指标未改善

**症状**: Per-Task 公平性 < 0.9

**原因**: 租户标签缺失或打分权重配置不合理

**解决**:
1. 确保 Pod 带有 `tenant.slo.io/id` 标签
2. 调整 `calculateScore` 中的权重（公平性奖励从 15 增加到 20）
3. 检查 Jain's Index 计算是否正确（日志中打印中间值）

## 参数调优

### scheduler-extender 调优

编辑 `scheduler-extender/main.go`:
```go
const (
    // 增加指标缓存时间，减少指标过期导致的降级
    metricsTTL = 5 * time.Minute  // 从 2min 改为 5min
    
    // 降低目标延迟阈值，提高对高延迟节点的惩罚
    targetTailLatencyMillis = 100.0  // 从 120ms 改为 100ms
)

// 调整打分权重
func calculateScore(pod *corev1.Pod, metrics nodeMetrics) int64 {
    base := getPriorityBaseScore(pod)
    capacityScore := ((cpuFreeRatio + memFreeRatio) / 2.0) * 35.0  // 降低容量权重
    fairnessScore := clampFloat(metrics.PerTaskFairness, 0, 1) * 20.0  // 提高公平性权重
    latencyScore := ... * 20.0
    dominancePenalty := ... * 20.0  // 增加占用惩罚
    violationPenalty := ... * 25.0  // 增加违约惩罚
    ...
}
```

### sidecar-agent 调优

环境变量调整:
```yaml
# 缩短采样间隔，提高指标新鲜度
- name: METRICS_SCRAPE_INTERVAL_SECS
  value: "15"  # 从 30s 改为 15s
```

## 下一步

1. **集成 eBPF 延迟采集**: 实现 `probes.rs`，使用 Aya 加载 kprobe 探针，采集真实应用延迟
2. **对接 credit-service**: 在 sidecar 中读取违约事件，上报到 credit-service
3. **Prometheus 导出**: 在 sidecar 暴露 `/metrics` 端点，便于可视化监控
4. **GPU 调度支持**: 启用 `--features gpu`，集成 nvml-wrapper
5. **自适应权重**: 根据历史数据自动调整 `calculateScore` 中的权重参数

## 参考

- Firmament 调度器: https://github.com/Huawei-Hadoop/firmament
- Mesos DRF: https://github.com/apache/mesos
- Kubernetes Scheduler Extender: https://kubernetes.io/docs/concepts/scheduling-eviction/scheduler-extender/
- cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

