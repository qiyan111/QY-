#!/usr/bin/env python3
"""JCT 与 SLO 违反率指标计算模块。

提供：
- build_job_index(tasks): 构建作业(job_id)到其实例信息的索引
- compute_job_times(task_timelines, job_index): 汇总作业提交/完成时间与JCT
- summarize_jct(job_times): 输出均值/中位数/P95等统计
- compute_slo_violations(job_times, job_types, thresholds): 计算总体与分类型 SLO 违反率

约定：
- job_id 使用 tasks 中的 tenant 字段（来自 Alibaba trace 的 job_id 列）
- 作业提交时间 = 该作业所有实例到达时间的最小值
- 作业完成时间 = 该作业所有实例完成时间的最大值
- JCT = 完成时间 - 提交时间
- task_timelines: {tid: {"submit": ts, "start": ts, "end": ts}}
"""
from __future__ import annotations
from typing import Dict, List, Tuple, Any
import numpy as np


def build_job_index(tasks: List[Any]) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """构建 job_id -> [instance_ids] 映射，以及 instance_id -> job_id 映射。
    任务对象需包含 id, tenant(job_id), arrival。
    """
    job_to_instances: Dict[str, List[str]] = {}
    inst_to_job: Dict[str, str] = {}
    for t in tasks:
        jid = str(getattr(t, "tenant", ""))
        iid = str(getattr(t, "id", ""))
        if not jid or not iid:
            continue
        job_to_instances.setdefault(jid, []).append(iid)
        inst_to_job[iid] = jid
    return job_to_instances, inst_to_job


def compute_job_times(
    task_timelines: Dict[str, Dict[str, int]],
    job_to_instances: Dict[str, List[str]],
    tasks: List[Any],
) -> Dict[str, Dict[str, float]]:
    """计算每个作业的提交/完成时间与 JCT。
    返回: job_times[job_id] = {"submit": s, "finish": f, "jct": f-s}
    """
    job_times: Dict[str, Dict[str, float]] = {}
    for job_id, inst_list in job_to_instances.items():
        submits = []
        finishes = []
        for iid in inst_list:
            tl = task_timelines.get(str(iid))
            if not tl:
                continue
            s = tl.get("submit")
            e = tl.get("end")
            if s is None or e is None:
                continue
            submits.append(s)
            finishes.append(e)
        if not submits or not finishes:
            continue
        submit = float(min(submits))
        finish = float(max(finishes))
        if finish >= submit:
            job_times[job_id] = {"submit": submit, "finish": finish, "jct": finish - submit}
    return job_times


def summarize_jct(job_times: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """对 JCT 做统计：平均、中位、P95、P99（单位：秒）。"""
    if not job_times:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    jcts = np.array([info["jct"] for info in job_times.values()], dtype=float)
    return {
        "avg": float(np.mean(jcts)),
        "p50": float(np.percentile(jcts, 50)),
        "p95": float(np.percentile(jcts, 95)),
        "p99": float(np.percentile(jcts, 99)),
    }


def build_job_types(tasks: List[Any]) -> Dict[str, str]:
    """构建作业类型：job_id -> type ("high"/"low").
    策略：若该作业任一实例 slo_sensitive=="high"，则作业为 high，否则 low。
    """
    job_type: Dict[str, str] = {}
    for t in tasks:
        jid = str(getattr(t, "tenant", ""))
        if not jid:
            continue
        cur = job_type.get(jid, "low")
        if getattr(t, "slo_sensitive", "low") == "high":
            cur = "high"
        job_type[jid] = cur
    return job_type


def compute_slo_violations(
    job_times: Dict[str, Dict[str, float]],
    job_types: Dict[str, str],
    thresholds: Dict[str, float],  # {"high": seconds, "low": seconds, "default": seconds}
) -> Dict[str, float]:
    """计算 SLO 违反率：总体与分类型。
    返回: {"overall": x, "high": xh, "low": xl}
    """
    if not job_times:
        return {"overall": 0.0, "high": 0.0, "low": 0.0}
    total = len(job_times)
    vio_total = 0
    # 分类型计数
    cat_counts = {"high": 0, "low": 0}
    cat_vios = {"high": 0, "low": 0}
    for jid, info in job_times.items():
        jct = info.get("jct", 0.0)
        cat = job_types.get(jid, "low")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        thr = thresholds.get(cat, thresholds.get("default", 600.0))
        if jct > thr:
            vio_total += 1
            cat_vios[cat] = cat_vios.get(cat, 0) + 1
    overall = vio_total / max(total, 1)
    high = (cat_vios.get("high", 0) / max(cat_counts.get("high", 1), 1)) if cat_counts.get("high", 0) > 0 else 0.0
    low = (cat_vios.get("low", 0) / max(cat_counts.get("low", 1), 1)) if cat_counts.get("low", 0) > 0 else 0.0
    return {"overall": overall, "high": high, "low": low}
