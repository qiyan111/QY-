use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use reqwest::Client;
use serde::Serialize;
use tokio::sync::mpsc;
use tokio::time::{interval, MissedTickBehavior};
use tokio::signal;

#[cfg(feature = "gpu")]
mod gpu;

#[cfg(feature = "ebpf")]
mod probes;

mod sys;

use crate::sys::{ContainerMetricSample, MetricsCollector};

const DEFAULT_INTERVAL_SECS: u64 = 30;
const DEFAULT_REPORT_URL: &str = "http://scheduler-extender:9001/metrics/report";

#[derive(Debug, Serialize, Clone)]
struct MetricsPayload {
    node_name: String,
    cpu_allocatable: f64,
    cpu_used: f64,
    memory_allocatable: f64,
    memory_used: f64,
    tail_latency_p99: f64,
    slo_violation_rate: f64,
    dominant_share: f64,
    per_task_fairness: f64,
    #[serde(rename = "gpu_used", skip_serializing_if = "Option::is_none")]
    gpu_used: Option<f64>,
    #[serde(rename = "gpu_allocatable", skip_serializing_if = "Option::is_none")]
    gpu_allocatable: Option<f64>,
    timestamp: chrono::DateTime<chrono::Utc>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let node_name = std::env::var("NODE_NAME").unwrap_or_else(|_| hostname::get().unwrap_or_default().to_string_lossy().to_string());
    let interval_seconds = std::env::var("METRICS_SCRAPE_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_SECS);
    let extender_url = std::env::var("EXTENDER_METRICS_URL")
        .unwrap_or_else(|_| DEFAULT_REPORT_URL.to_string());

    let kubelet_root = PathBuf::from(std::env::var("KUBELET_ROOT").unwrap_or_else(|_| "/var/lib/kubelet".into()));
    let sysfs_root = PathBuf::from(std::env::var("SYSFS_ROOT").unwrap_or_else(|_| "/sys/fs/cgroup".into()));

    let collector = MetricsCollector::new(kubelet_root, sysfs_root).context("initialize metrics collector")?;

    let client = Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .context("build http client")?;

    let (tx, mut rx) = mpsc::channel(16);

    let mut ticker = interval(Duration::from_secs(interval_seconds));
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);

    tokio::spawn(async move {
        let mut ticker = ticker;
        loop {
            ticker.tick().await;
            if tx.send(()).await.is_err() {
                break;
            }
        }
    });

    println!("sidecar-agent started on node {}", node_name);

    loop {
        tokio::select! {
            biased;
            _ = wait_for_shutdown() => {
                println!("received shutdown signal, exiting");
                break;
            }
            Some(_) = rx.recv() => {
                match collect_and_report(&collector, &client, &node_name, &extender_url).await {
                    Ok(_) => {}
                    Err(err) => eprintln!("failed to report metrics: {err:?}"),
                }
            }
            else => break,
        }
    }

    Ok(())
}

async fn wait_for_shutdown() {
    #[cfg(unix)]
    {
        let mut sigterm = signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler");
        let mut sigint = signal::unix::signal(signal::unix::SignalKind::interrupt())
            .expect("install SIGINT handler");
        tokio::select! {
            _ = sigterm.recv() => {},
            _ = sigint.recv() => {},
        }
    }
    #[cfg(not(unix))]
    {
        let _ = signal::ctrl_c().await;
    }
}

async fn collect_and_report(collector: &MetricsCollector, client: &Client, node_name: &str, extender_url: &str) -> Result<()> {
    let samples = collector.collect().await?;
    if samples.is_empty() {
        return Ok(());
    }

    let aggregated = aggregate_samples(&samples);
    let payload = MetricsPayload {
        node_name: node_name.to_string(),
        cpu_allocatable: aggregated.cpu_allocatable,
        cpu_used: aggregated.cpu_used,
        memory_allocatable: aggregated.memory_allocatable,
        memory_used: aggregated.memory_used,
        tail_latency_p99: aggregated.tail_latency_p99,
        slo_violation_rate: aggregated.slo_violation_rate,
        dominant_share: aggregated.dominant_share,
        per_task_fairness: aggregated.per_task_fairness,
        gpu_used: aggregated.gpu_used,
        gpu_allocatable: aggregated.gpu_allocatable,
        timestamp: chrono::DateTime::<chrono::Utc>::from(SystemTime::now()),
    };

    client
        .post(extender_url)
        .json(&payload)
        .send()
        .await
        .context("send metrics request")?
        .error_for_status()
        .context("unexpected response status")?;

    Ok(())
}

struct AggregatedMetrics {
    cpu_allocatable: f64,
    cpu_used: f64,
    memory_allocatable: f64,
    memory_used: f64,
    tail_latency_p99: f64,
    slo_violation_rate: f64,
    dominant_share: f64,
    per_task_fairness: f64,
    gpu_used: Option<f64>,
    gpu_allocatable: Option<f64>,
}

fn aggregate_samples(samples: &[ContainerMetricSample]) -> AggregatedMetrics {
    let mut cpu_alloc = 0.0;
    let mut cpu_used = 0.0;
    let mut mem_alloc = 0.0;
    let mut mem_used = 0.0;
    let mut latencies = Vec::new();
    let mut violation_total = 0.0;
    let mut violation_count = 0.0;
    let mut tenant_usage: HashMap<String, (f64, f64)> = HashMap::new();

    #[cfg(feature = "gpu")]
    let mut gpu_alloc = 0.0;
    #[cfg(feature = "gpu")]
    let mut gpu_used_sum = 0.0;

    for sample in samples {
        cpu_alloc += sample.cpu_limit_cores;
        cpu_used += sample.cpu_usage_cores;
        mem_alloc += sample.memory_limit_gib;
        mem_used += sample.memory_usage_gib;

        if let Some(p99) = sample.tail_latency_p99_ms {
            latencies.push(p99);
        }
        if let Some((violations, total)) = sample.slo_violation {
            violation_total += violations;
            violation_count += total;
        }

        let key = sample.tenant_id.clone().unwrap_or_else(|| "default".into());
        let entry = tenant_usage.entry(key).or_insert((0.0, 0.0));
        entry.0 += sample.cpu_usage_cores;
        entry.1 += sample.memory_usage_gib;

        #[cfg(feature = "gpu")]
        if let Some(gpu) = sample.gpu_usage {
            gpu_used_sum += gpu.used;
            gpu_alloc += gpu.allocatable;
        }
    }

    let tail_latency_p99 = calculate_percentile(&mut latencies, 0.99);
    let slo_violation_rate = if violation_count > 0.0 {
        (violation_total / violation_count).clamp(0.0, 1.0)
    } else {
        0.0
    };

    let dominant_share = if cpu_alloc > 0.0 || mem_alloc > 0.0 {
        let cpu_ratio = if cpu_alloc > 0.0 { cpu_used / cpu_alloc } else { 0.0 };
        let mem_ratio = if mem_alloc > 0.0 { mem_used / mem_alloc } else { 0.0 };
        cpu_ratio.max(mem_ratio).clamp(0.0, 1.5)
    } else {
        0.0
    };

    let per_task_fairness = jains_index(&tenant_usage);

    AggregatedMetrics {
        cpu_allocatable: cpu_alloc.max(num_cpus::get() as f64),
        cpu_used,
        memory_allocatable: mem_alloc.max(1.0),
        memory_used,
        tail_latency_p99,
        slo_violation_rate,
        dominant_share,
        per_task_fairness,
        gpu_used: {
            #[cfg(feature = "gpu")]
            {
                if gpu_alloc > 0.0 {
                    Some((gpu_used_sum / samples.len() as f64).clamp(0.0, gpu_alloc))
                } else {
                    None
                }
            }
            #[cfg(not(feature = "gpu"))]
            {
                None
            }
        },
        gpu_allocatable: {
            #[cfg(feature = "gpu")]
            {
                if gpu_alloc > 0.0 { Some(gpu_alloc) } else { None }
            }
            #[cfg(not(feature = "gpu"))]
            {
                None
            }
        },
    }
}

fn calculate_percentile(values: &mut Vec<f64>, percentile: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = ((values.len() as f64) * percentile).ceil() as usize;
    let idx = idx.saturating_sub(1).min(values.len() - 1);
    values[idx]
}

fn jains_index(tenant_usage: &HashMap<String, (f64, f64)>) -> f64 {
    if tenant_usage.is_empty() {
        return 1.0;
    }
    let mut sum = 0.0;
    let mut sum_sq = 0.0;
    for usage in tenant_usage.values() {
        let dominant = usage.0.max(usage.1);
        sum += dominant;
        sum_sq += dominant * dominant;
    }
    if sum_sq == 0.0 {
        1.0
    } else {
        (sum * sum) / (tenant_usage.len() as f64 * sum_sq)
    }
}

fn unix_ts() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|_| Duration::from_secs(0))
        .as_secs()
}

