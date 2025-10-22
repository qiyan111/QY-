# Weighted DRF + Knapsack Scheduling

## Overview
We decompose the placement decision into two stages:

1. **Weighted Dominant Resource Fairness (wDRF)** to allocate *shares* among tenants.
2. **0-1 Knapsack** on each candidate node to maximise utilisation under share constraints.

## Stage 1: wDRF
For each tenant \(t) we define a credit-coupled weight:
```
w_t = f(credit_t) = 0.5 + 0.5 * credit_t    # credit_t ∈ [0,1], w_t ∈ [0.5,1.0]
```
To couple fairness with SLO risk, we amplify weight differences when the global risk is high:
```
γ(risk) = 1 + β * max(0, risk - 0.02),  clipped to [1, 2]
w_t' = (w_t)^{γ(risk)}
```
Higher credit ⇒ larger weight ⇒ smaller share ⇒ higher priority under DRF.

The algorithm selects a set of nodes where the incoming Pod could fit and computes the dominant share:
```
share_t = max( cpu_request / cpu_alloc , mem_request / mem_alloc ) / w_t'
```
Pods of tenants with lower `share_t` are preferred (fairness).

## Stage 2: Knapsack per Node
Given a node’s residual capacity vector \((C_{cpu}, C_{mem})\) and list of candidate Pods, solve:
```
max Σ u_i x_i
s.t. Σ cpu_i x_i ≤ C_cpu
     Σ mem_i x_i ≤ C_mem
     x_i ∈ {0,1}
```
Utility \(u_i) is proportional to `w_t / share_t` so that high-credit, low-share pods score higher.

A greedy heuristic (sort by `u_i / resource` ratio) runs in O(k log k) where k is candidate count.

Note: In practice, we first apply a lightweight Tetris/worst-fit scoring to select Top-K candidate nodes per task (Hybrid prefilter), then apply wDRF + knapsack to the reduced set to improve scalability and reduce fragmentation.
