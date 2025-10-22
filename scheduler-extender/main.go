package main

import (
    "encoding/json"
    "fmt"
    "log"
    "math"
    "net/http"
    "sync"
    "time"

    corev1 "k8s.io/api/core/v1"
    "k8s.io/apimachinery/pkg/api/resource"
)

const (
    defaultCPURequestCores   = 0.1
    defaultMemoryRequestGiB  = 0.2
    metricsTTL               = 2 * time.Minute
    targetTailLatencyMillis  = 100.0  // 降低目标延迟阈值以更严格地惩罚高延迟节点
    highUtilizationThreshold = 0.8    // 高利用率阈值
)

var priorityBaseScore = map[string]float64{
    "high-priority":   35,
    "medium-priority": 25,
    "low-priority":    15,
}

type nodeMetrics struct {
    NodeName          string    `json:"nodeName"`
    CPUAllocatable    float64   `json:"cpuAllocatable"`
    CPUUsed           float64   `json:"cpuUsed"`
    MemoryAllocatable float64   `json:"memoryAllocatable"`
    MemoryUsed        float64   `json:"memoryUsed"`
    TailLatencyP99    float64   `json:"tailLatencyP99"`
    SLOViolationRate  float64   `json:"sloViolationRate"`
    DominantShare     float64   `json:"dominantShare"`
    PerTaskFairness   float64   `json:"perTaskFairness"`
    Timestamp         time.Time `json:"timestamp"`
}

type metricsCache struct {
    sync.RWMutex
    data map[string]nodeMetrics
}

func newMetricsCache() *metricsCache {
    return &metricsCache{data: make(map[string]nodeMetrics)}
}

func (c *metricsCache) set(m nodeMetrics) {
    c.Lock()
    defer c.Unlock()
    c.data[m.NodeName] = m
}

func (c *metricsCache) get(name string) (nodeMetrics, bool) {
    c.RLock()
    defer c.RUnlock()
    m, ok := c.data[name]
    return m, ok
}

func (c *metricsCache) snapshot() map[string]nodeMetrics {
    c.RLock()
    defer c.RUnlock()
    snapshot := make(map[string]nodeMetrics, len(c.data))
    for k, v := range c.data {
        snapshot[k] = v
    }
    return snapshot
}

var nodeMetricsStore = newMetricsCache()

func reportMetrics(w http.ResponseWriter, r *http.Request) {
    defer r.Body.Close()
    var payload nodeMetrics
    if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
        http.Error(w, fmt.Sprintf("invalid metrics payload: %v", err), http.StatusBadRequest)
        return
    }
    if payload.NodeName == "" {
        http.Error(w, "missing nodeName", http.StatusBadRequest)
        return
    }
    if payload.Timestamp.IsZero() {
        payload.Timestamp = time.Now().UTC()
    }
    nodeMetricsStore.set(payload)
    w.WriteHeader(http.StatusAccepted)
}

func filter(w http.ResponseWriter, r *http.Request) {
    defer r.Body.Close()
    var args ExtenderArgs
    if err := json.NewDecoder(r.Body).Decode(&args); err != nil {
        http.Error(w, err.Error(), http.StatusBadRequest)
        return
    }

    cpuReq, memReq := getPodResourceRequest(args.Pod)
    filtered := &corev1.NodeList{Items: make([]corev1.Node, 0, len(args.Nodes.Items))}
    failed := make(map[string]string)

    for _, node := range args.Nodes.Items {
        if ok, reason := nodeCanFit(&node, cpuReq, memReq); ok {
            filtered.Items = append(filtered.Items, node)
        } else if reason != "" {
            failed[node.Name] = reason
        } else {
            failed[node.Name] = "capacity check failed"
        }
    }

    result := &ExtenderFilterResult{
        Nodes:       filtered,
        FailedNodes: failed,
    }
    if err := json.NewEncoder(w).Encode(result); err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
    }
}

func prioritize(w http.ResponseWriter, r *http.Request) {
    defer r.Body.Close()
    var args ExtenderArgs
    if err := json.NewDecoder(r.Body).Decode(&args); err != nil {
        http.Error(w, err.Error(), http.StatusBadRequest)
        return
    }

    snapshot := nodeMetricsStore.snapshot()
    pod := args.Pod
    scores := make([]HostPriority, len(args.Nodes.Items))

    for i, node := range args.Nodes.Items {
        if metrics, ok := snapshot[node.Name]; ok && time.Since(metrics.Timestamp) <= metricsTTL {
            scores[i] = HostPriority{Host: node.Name, Score: calculateScore(&pod, metrics)}
        } else {
            base := int64(math.Round(getPriorityBaseScore(&pod)))
            scores[i] = HostPriority{Host: node.Name, Score: clampScore(base)}
        }
    }

    if err := json.NewEncoder(w).Encode(scores); err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
    }
}

func nodeCanFit(node *corev1.Node, cpuReq, memReq float64) (bool, string) {
    metrics, ok := nodeMetricsStore.get(node.Name)
    if ok && !metrics.Timestamp.IsZero() && time.Since(metrics.Timestamp) <= metricsTTL {
        cpuFree := metrics.CPUAllocatable - metrics.CPUUsed
        memFree := metrics.MemoryAllocatable - metrics.MemoryUsed
        if cpuFree >= cpuReq && memFree >= memReq {
            return true, ""
        }
        // If metrics look suspicious (negative/NaN or alloc<used), fall through to allocatable check.
        if cpuFree >= 0 && memFree >= 0 && metrics.CPUAllocatable >= metrics.CPUUsed && metrics.MemoryAllocatable >= metrics.MemoryUsed {
            if cpuFree < cpuReq {
                return false, fmt.Sprintf("CPU insufficient: free %.2f cores < req %.2f cores", cpuFree, cpuReq)
            }
            if memFree < memReq {
                return false, fmt.Sprintf("Memory insufficient: free %.2f GiB < req %.2f GiB", memFree, memReq)
            }
        }
    }

    cpuAlloc := quantityToCores(node.Status.Allocatable[corev1.ResourceCPU])
    memAlloc := quantityToGiB(node.Status.Allocatable[corev1.ResourceMemory])
    if cpuAlloc < cpuReq {
        return false, fmt.Sprintf("CPU allocatable %.2f cores < req %.2f cores", cpuAlloc, cpuReq)
    }
    if memAlloc < memReq {
        return false, fmt.Sprintf("Memory allocatable %.2f GiB < req %.2f GiB", memAlloc, memReq)
    }
    return true, ""
}

func calculateScore(pod *corev1.Pod, metrics nodeMetrics) int64 {
    base := getPriorityBaseScore(pod)

    cpuCapacity := math.Max(metrics.CPUAllocatable, 0.1)
    memCapacity := math.Max(metrics.MemoryAllocatable, 0.1)
    cpuFreeRatio := safeRatio(metrics.CPUAllocatable-metrics.CPUUsed, cpuCapacity)
    memFreeRatio := safeRatio(metrics.MemoryAllocatable-metrics.MemoryUsed, memCapacity)
    // 降低容量权重: 40 → 30，避免过度追求高利用率
    capacityScore := ((cpuFreeRatio + memFreeRatio) / 2.0) * 30.0

    fairnessScore := clampFloat(metrics.PerTaskFairness, 0, 1) * 15.0
    dominancePenalty := clampFloat(metrics.DominantShare, 0, 1) * 15.0

    // 提高延迟权重: 20 → 25，增强对高延迟节点的惩罚
    latencyScore := 0.0
    if metrics.TailLatencyP99 > 0 {
        ratio := metrics.TailLatencyP99 / targetTailLatencyMillis
        latencyScore = (1.0 - math.Min(ratio, 1.5)) * 25.0
    } else {
        // 缺失延迟数据时给予中等分数，避免过度奖励
        latencyScore = 12.5
    }

    // 大幅提高违约惩罚: 20 → 30，降低违约率
    violationPenalty := 0.0
    if metrics.SLOViolationRate > 0 {
        violationPenalty = math.Min(metrics.SLOViolationRate*30.0, 30.0)
    } else {
        // 缺失违约数据时假设有一定风险，给予保守惩罚
        violationPenalty = 5.0
    }

    // 新增：高利用率惩罚，避免过度打包导致SLO违约
    avgUtilization := (safeRatio(metrics.CPUUsed, cpuCapacity) + safeRatio(metrics.MemoryUsed, memCapacity)) / 2.0
    utilizationPenalty := 0.0
    if avgUtilization > highUtilizationThreshold {
        utilizationPenalty = (avgUtilization - highUtilizationThreshold) * 50.0  // 最多扣10分
    }

    final := base + capacityScore + fairnessScore + latencyScore - dominancePenalty - violationPenalty - utilizationPenalty
    return clampScore(int64(math.Round(final)))
}

func getPriorityBaseScore(pod *corev1.Pod) float64 {
    score := priorityBaseScore[pod.Spec.PriorityClassName]
    if score == 0 {
        score = priorityBaseScore["low-priority"]
        if score == 0 {
            score = 15
        }
    }
    return score
}

func clampScore(score int64) int64 {
    if score < 0 {
        return 0
    }
    if score > 100 {
        return 100
    }
    return score
}

func clampFloat(val, min, max float64) float64 {
    if val < min {
        return min
    }
    if val > max {
        return max
    }
    return val
}

func safeRatio(numerator, denominator float64) float64 {
    if denominator <= 0 {
        return 0
    }
    v := numerator / denominator
    if math.IsInf(v, 0) || math.IsNaN(v) {
        return 0
    }
    if v < 0 {
        return 0
    }
    if v > 1 {
        return 1
    }
    return v
}

func getPodResourceRequest(pod corev1.Pod) (float64, float64) {
    var cpuSum, memSum float64
    for _, c := range pod.Spec.Containers {
        cpuSum += quantityToCores(c.Resources.Requests[corev1.ResourceCPU])
        memSum += quantityToGiB(c.Resources.Requests[corev1.ResourceMemory])
    }

    var initCPU, initMem float64
    for _, c := range pod.Spec.InitContainers {
        initCPU = math.Max(initCPU, quantityToCores(c.Resources.Requests[corev1.ResourceCPU]))
        initMem = math.Max(initMem, quantityToGiB(c.Resources.Requests[corev1.ResourceMemory]))
    }

    cpuReq := math.Max(cpuSum, initCPU)
    memReq := math.Max(memSum, initMem)

    if cpuReq == 0 {
        cpuReq = defaultCPURequestCores
    }
    if memReq == 0 {
        memReq = defaultMemoryRequestGiB
    }
    return cpuReq, memReq
}

func quantityToCores(q resource.Quantity) float64 {
    if q.IsZero() {
        return 0
    }
    return q.AsApproximateFloat64()
}

func quantityToGiB(q resource.Quantity) float64 {
    if q.IsZero() {
        return 0
    }
    // Quantity stores bytes for memory; convert to GiB.
    return float64(q.Value()) / (1024 * 1024 * 1024)
}

func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/filter", filter)
    mux.HandleFunc("/prioritize", prioritize)
    mux.HandleFunc("/metrics/report", reportMetrics)

    log.Println("scheduler-extender listening on :9001")
    if err := http.ListenAndServe(":9001", mux); err != nil {
        log.Fatalf("server failed: %v", err)
    }
}
