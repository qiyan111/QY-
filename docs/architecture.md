# System Architecture

```
+-------------+
|  Users      |
+------+------+
       |
       v
+------------------+     Admission     +-------------------+
| kube-apiserver   |---> Controller --->|  Scheduler        |
+------------------+                   /|  (extender)      |
                                       / +-----------------+
+------------------+                  /
| credit-service   |<-----------------/  gRPC
+------------------+
       ^                             
       |                             
+------+-------+  metrics  +-------------------+
| sidecar-agent |--------->| cgroup-adjuster   |
+-------------- +          +-------------------+
```

* **credit-service** – Maintains real-time tenant credit derived from SLO history.
* **admission-controller** – Mutates incoming Pods with `priorityClassName` based on credit.
* **scheduler-extender** – Applies DRF/Knapsack scoring to prioritize nodes.
* **sidecar-agent** – eBPF daemon collecting queue length & tail latency per Pod.
* **cgroup-adjuster** – Tunes CPU quotas according to credit and performance signal.

Data flows:
1. Sidecar pushes metrics to adjuster; adjuster updates cgroups and may emit violation events.
2. Violation events are sent to credit-service (RecordViolation RPC), adjusting score.
3. When new Pods are created, admission-controller fetches credit and sets priority.
4. Scheduler extender uses priority to bias node selection.
