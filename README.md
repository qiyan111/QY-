# SLO-Driven Multi-Tenant Microservice Scheduler

This repository hosts a proof-of-concept reference implementation of an SLO-aware, credit-based scheduler for Kubernetes clusters running multi-tenant microservices.

## Problem Statement
Different tenants expose diverse Service-Level Objectives (SLOs)—for example p99 latency and throughput targets. Applying a single resource allocation threshold risks either starving low-priority tenants or leaving valuable CPU cycles idle. This project explores how to:

1. Quantify tenant "credit" from declared SLOs, historical violations, and live performance signals.
2. Inject a credit-weighted DRF / knapsack optimisation step into the scheduling admission path.
3. Enforce tail-latency–aware resource isolation at node level via eBPF-driven cgroup shaping.

## Repository Layout
| Path | Purpose |
|------|---------|
| `credit-service/` | Go microservice that computes and exposes real-time tenant credit. |
| `admission-controller/` | Webhook that solves the weighted DRF/Knapsack and annotates pods before kube-scheduler sees them. |
| `scheduler-extender/` | Optional scheduler framework plugin/extender that honours the annotations. |
| `sidecar-agent/` | Rust/ebpf daemonset collecting queue length & tail-latency; performs on-node resource adjustments. |
| `charts/` | Helm charts for quick deployment on a Kind or real cluster. |
| `docs/` | Design documents, diagrams, and benchmark results. |

## Quick Start (WIP)
Development instructions will appear once the individual components are scaffolded.

---
Created by the AI pair-programming assistant.
