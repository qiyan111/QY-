use anyhow::{anyhow, Context, Result};
use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
struct PodStatus {
    status: Option<StatusSection>,
}

#[derive(Debug, Deserialize)]
struct StatusSection {
    #[serde(default)]
    phase: Option<String>,
    #[serde(default)]
    qosClass: Option<String>,
}

#[derive(Debug, Clone)]
pub struct PodCgroupInfo {
    pub namespace: String,
    pub name: String,
    pub uid: String,
    pub qos_class: Option<String>,
    pub cgroup_path: PathBuf,
    pub tenant_id: Option<String>,
}

pub fn discover_pods(kubelet_root: &Path, cgroup_root: &Path) -> Result<Vec<PodCgroupInfo>> {
    let pods_root = kubelet_root.join("pods");
    let entries = fs::read_dir(&pods_root).context("read pods directory")?;

    let mut infos = Vec::new();

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        if let Some(info) = build_pod_info(&path, cgroup_root).transpose()? {
            infos.push(info);
        }
    }

    Ok(infos)
}

fn build_pod_info(pod_dir: &Path, cgroup_root: &Path) -> Result<Option<PodCgroupInfo>> {
    let metadata_path = pod_dir.join("pod.info");
    if !metadata_path.exists() {
        return Ok(None);
    }

    let metadata_bytes = fs::read(&metadata_path).context("read pod.info")?;
    let metadata: serde_json::Value = serde_json::from_slice(&metadata_bytes).context("parse pod.info JSON")?;

    let uid = metadata.get("metadata").and_then(|m| m.get("uid")).and_then(|v| v.as_str());
    let namespace = metadata
        .get("metadata")
        .and_then(|m| m.get("namespace"))
        .and_then(|v| v.as_str())
        .unwrap_or("default");
    let name = metadata
        .get("metadata")
        .and_then(|m| m.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");

    let status: PodStatus = serde_json::from_value(
        metadata
            .get("status")
            .cloned()
            .unwrap_or_else(|| serde_json::json!({})),
    )?;

    let qos_class = status.status.and_then(|s| s.qosClass);

    let cgroup_rel = pod_dir.join("etc2").join("podCgroupRelativePath");
    let relative = fs::read_to_string(&cgroup_rel).unwrap_or_else(|_| "kubepods.slice".to_string());
    let mut cgroup_path = cgroup_root.join(relative.trim());
    if !cgroup_path.exists() {
        cgroup_path = cgroup_root.join("kubepods.slice");
    }

    let tenant_id = metadata
        .get("metadata")
        .and_then(|m| m.get("labels"))
        .and_then(|labels| labels.get("tenant.slo.io/id"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    Ok(Some(PodCgroupInfo {
        namespace: namespace.to_string(),
        name: name.to_string(),
        uid: uid.unwrap_or_default().to_string(),
        qos_class,
        tenant_id,
        cgroup_path,
    }))
}

