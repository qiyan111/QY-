# Tenant Credit Model

The scheduler uses a scalar **credit score** \(0–1\] to represent the reliability posture of each tenant. The score is recomputed every time the credit-service receives a violation event.

## Inputs
| Symbol | Meaning |
|--------|---------|
| \(E\_t) | error-budget remaining of tenant **t** (fraction 0–1) |
| \(V\_t) | cumulative number of SLO violations in current window |
| \(R\_t) | recent tail-latency ratio = observed p99 / target p99 |
| \(\alpha,\,\beta,\,\gamma) | tunable weights |

## Formula
```
credit_t = clamp( 1 − α·(1−E_t) − β·V_t/V_max − γ·max(0, R_t−1) , 0 , 1 )
```
* **Error-budget term**: consumes score linearly as budget is depleted.
* **Violation term**: normalises by the worst case \(V\_{max}).
* **Tail-latency term**: penalises tenants currently over target latency.

Default weights: \(α=0.5, β=0.3, γ=0.2). Values can be learned offline with Bayesian optimisation.

## Update Algorithm (credit-service)
1. On `RecordViolation`, increment \(V\_t) and recompute `credit_t`.
2. Periodically (default 1 min) pull live metrics from sidecar-agent to refresh \(R\_t).
3. Expose `TenantCredit` via gRPC; admission-controller & adjuster query on demand.
