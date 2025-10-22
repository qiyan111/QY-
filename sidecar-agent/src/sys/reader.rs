use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result};
use tokio::task;

use super::pod::{discover_pods, PodCgroupInfo};

#[derive(Debug, Clone)]
pub struct ContainerMetricSample {
    pub cpu_usage_cores: f64,
    pub cpu_limit_cores: f64,
    pub memory_usage_gib: f64,
    pub memory_limit_gib: f64,
    pub tail_latency_p99_ms: Option<f64>,
    pub slo_violation: Option<(f64, f64)>,
    pub tenant_id: Option<String>,
    #[cfg(feature = "gpu")]
    pub gpu_usage: Option<crate::gpu::GpuStats>,
}

pub struct MetricsCollector {
    kubelet_root: PathBuf,
    cgroup_root: PathBuf,
}

impl MetricsCollector {
    pub fn new(kubelet_root: PathBuf, cgroup_root: PathBuf) -> Result<Self> {
        Ok(Self {
            kubelet_root,
            cgroup_root,
        })
    }

    pub async fn collect(&self) -> Result<Vec<ContainerMetricSample>> {
        let kubelet = self.kubelet_root.clone();
        let cgroup_root = self.cgroup_root.clone();

        task::spawn_blocking(move || self.collect_sync(&kubelet, &cgroup_root))
            .await
            .context("collect metrics task")?
    }

    fn collect_sync(&self, kubelet: &Path, cgroup_root: &Path) -> Result<Vec<ContainerMetricSample>> {
        let pods = discover_pods(kubelet, cgroup_root)?;
        let mut result = Vec::new();

        for pod in pods {
            if let Some(sample) = self.collect_pod_metrics(&pod).context("collect pod metrics")? {
                result.push(sample);
            }
        }

        Ok(result)
    }

    fn collect_pod_metrics(&self, pod: &PodCgroupInfo) -> Result<Option<ContainerMetricSample>> {
        let cpu_usage = read_cpu_usage(&pod.cgroup_path).unwrap_or(0.0);
        let cpu_limit = read_cpu_limit(&pod.cgroup_path).unwrap_or(num_cpus::get() as f64);
        if cpu_limit <= 0.0 {
            return Ok(None);
        }

        let mem_usage = read_memory_usage(&pod.cgroup_path).unwrap_or(0.0);
        let mem_limit = read_memory_limit(&pod.cgroup_path).unwrap_or(0.0);

        let tail_latency = read_latency_metric(pod).unwrap_or(None);
        let slo_violation = read_violation_metric(pod).unwrap_or(None);

        #[cfg(feature = "gpu")]
        let gpu_stats = crate::gpu::collect_gpu_stats().unwrap_or(None);

        Ok(Some(ContainerMetricSample {
            cpu_usage_cores: cpu_usage,
            cpu_limit_cores: cpu_limit,
            memory_usage_gib: mem_usage,
            memory_limit_gib: mem_limit,
            tail_latency_p99_ms: tail_latency,
            slo_violation,
            tenant_id: pod.tenant_id.clone(),
            #[cfg(feature = "gpu")]
            gpu_usage: gpu_stats,
        }))
    }
}

fn read_cpu_usage(cgroup_path: &Path) -> Option<f64> {
    let stat_path = cgroup_path.join("cpu.stat");
    let content = fs::read_to_string(stat_path).ok()?;
    let mut usage_ns = 0.0;
    for line in content.lines() {
        if let Some(value) = line.strip_prefix("usage_usec ") {
            if let Ok(val) = value.trim().parse::<f64>() {
                usage_ns = val * 1000.0;
                break;
            }
        }
        if let Some(value) = line.strip_prefix("usage_nsec ") {
            usage_ns = value.trim().parse::<f64>().unwrap_or(0.0);
            break;
        }
    }
    let seconds = (usage_ns / 1e9).max(0.0);
    Some(seconds)
}

fn read_cpu_limit(cgroup_path: &Path) -> Option<f64> {
    let max_path = cgroup_path.join("cpu.max");
    let content = fs::read_to_string(max_path).ok()?;
    let mut parts = content.split_whitespace();
    let quota = parts.next()?.parse::<f64>().ok()?;
    let period = parts.next()?.parse::<f64>().ok()?;
    if quota < 0.0 {
        Some(num_cpus::get() as f64)
    } else {
        Some(quota / period)
    }
}

fn read_memory_usage(cgroup_path: &Path) -> Option<f64> {
    let path = cgroup_path.join("memory.current");
    let bytes = fs::read_to_string(path).ok()?.trim().parse::<f64>().ok()?;
    Some(bytes / (1024.0 * 1024.0 * 1024.0))
}

fn read_memory_limit(cgroup_path: &Path) -> Option<f64> {
    let path = cgroup_path.join("memory.max");
    let content = fs::read_to_string(path).ok()?;
    if content.trim() == "max" {
        Some(0.0)
    } else {
        let bytes = content.trim().parse::<f64>().ok()?;
        Some(bytes / (1024.0 * 1024.0 * 1024.0))
    }
}

fn read_latency_metric(_pod: &PodCgroupInfo) -> Result<Option<f64>> {
    // TODO: integrate with probes module or app exporter.
    Ok(None)
}

fn read_violation_metric(_pod: &PodCgroupInfo) -> Result<Option<(f64, f64)>> {
    // TODO: integrate with credit-service feedback or eBPF maps.
    Ok(None)
}


