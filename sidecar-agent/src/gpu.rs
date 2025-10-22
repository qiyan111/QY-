use anyhow::Result;

#[derive(Debug, Clone, Copy)]
pub struct GpuStats {
    pub used: f64,
    pub allocatable: f64,
}

pub fn collect_gpu_stats() -> Result<Option<GpuStats>> {
    // TODO: integrate nvml-wrapper for real GPU usage.
    Ok(None)
}


