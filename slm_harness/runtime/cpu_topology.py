"""
Pure-CPU Hardware Topology Detector & Thread/SIMD Auto-Tuner.

Detects physical performance cores (Apple Silicon P-cores vs E-cores, x86 AVX2/AVX-512/VNNI),
physical RAM availability, and configures OpenMP / MKL / ONNX Runtime thread affinity
so CPU inference runs at peak throughput without hyperthread or E-core thrashing.
"""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class CPUTopology:
    arch: str
    brand: str
    logical_cores: int
    physical_cores: int
    performance_cores: int
    efficiency_cores: int
    simd_features: List[str] = field(default_factory=list)
    total_ram_mb: float = 8192.0
    available_ram_mb: float = 4096.0
    recommended_inference_threads: int = 4
    recommended_background_threads: int = 1

    def to_dict(self) -> Dict[str, object]:
        return {
            "arch": self.arch,
            "brand": self.brand,
            "logical_cores": self.logical_cores,
            "physical_cores": self.physical_cores,
            "performance_cores": self.performance_cores,
            "efficiency_cores": self.efficiency_cores,
            "simd_features": self.simd_features,
            "total_ram_mb": round(self.total_ram_mb, 1),
            "available_ram_mb": round(self.available_ram_mb, 1),
            "recommended_inference_threads": self.recommended_inference_threads,
            "recommended_background_threads": self.recommended_background_threads,
        }


def _sysctl_str(key: str) -> Optional[str]:
    try:
        res = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def detect_cpu_topology(override_threads: Optional[int] = None) -> CPUTopology:
    system = platform.system()
    arch = platform.machine().lower()
    logical = os.cpu_count() or 4
    physical = logical
    if psutil is not None:
        try:
            physical = psutil.cpu_count(logical=False) or logical
        except Exception:
            physical = logical

    brand = platform.processor() or arch
    p_cores = physical
    e_cores = 0
    simd: List[str] = []

    if system == "Darwin":
        b = _sysctl_str("machdep.cpu.brand_string")
        if b:
            brand = b
        p_val = _sysctl_str("hw.perflevel0.physicalcpu")
        e_val = _sysctl_str("hw.perflevel1.physicalcpu")
        if p_val and p_val.isdigit():
            p_cores = int(p_val)
        if e_val and e_val.isdigit():
            e_cores = int(e_val)
        if "arm" in arch or "aarch64" in arch:
            simd.extend(["ARM_NEON", "FP16", "DOTPROD"])
            i8mm = _sysctl_str("hw.optional.arm.FEAT_I8MM")
            if i8mm == "1":
                simd.append("I8MM")
            bf16 = _sysctl_str("hw.optional.arm.FEAT_BF16")
            if bf16 == "1":
                simd.append("BF16")
        else:
            feats = (_sysctl_str("machdep.cpu.features") or "") + " " + (_sysctl_str("machdep.cpu.leaf7_features") or "")
            for flag in ("AVX2", "AVX512", "FMA", "VNNI"):
                if flag in feats.upper():
                    simd.append(flag)
    elif system == "Linux":
        try:
            if os.path.exists("/proc/cpuinfo"):
                with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
                    cpuinfo = f.read()
                for line in cpuinfo.splitlines():
                    if "model name" in line and ":" in line:
                        brand = line.split(":", 1)[1].strip()
                        break
                low = cpuinfo.lower()
                for flag, label in [
                    ("avx512_vnni", "AVX512_VNNI"),
                    ("avx512f", "AVX512"),
                    ("avx_vnni", "AVX_VNNI"),
                    ("avx2", "AVX2"),
                    ("fma", "FMA"),
                    ("asimd", "ARM_NEON"),
                    ("neon", "ARM_NEON"),
                ]:
                    if flag in low and label not in simd:
                        simd.append(label)
        except Exception:
            pass

    if not simd:
        simd.append("SIMD_GENERIC")

    total_ram_mb = 8192.0
    avail_ram_mb = 4096.0
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            total_ram_mb = vm.total / (1024 * 1024)
            avail_ram_mb = vm.available / (1024 * 1024)
        except Exception:
            pass

    # Detect Linux cgroup memory limit (e.g., Hugging Face Spaces 16 GB container on a 128 GB AWS host)
    try:
        cg_lim_bytes = 0
        if os.path.exists("/sys/fs/cgroup/memory.max"):
            raw_max = open("/sys/fs/cgroup/memory.max", "r", encoding="utf-8").read().strip()
            if raw_max.isdigit():
                cg_lim_bytes = int(raw_max)
        elif os.path.exists("/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            raw_lim = open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r", encoding="utf-8").read().strip()
            if raw_lim.isdigit():
                cg_lim_bytes = int(raw_lim)
        if 0 < cg_lim_bytes < (64 * 1024 * 1024 * 1024):
            cg_total_mb = cg_lim_bytes / (1024 * 1024)
            rss_now = get_process_rss_mb()
            total_ram_mb = cg_total_mb
            avail_ram_mb = max(512.0, cg_total_mb - rss_now)
        elif os.environ.get("SPACE_ID"):
            rss_now = get_process_rss_mb()
            total_ram_mb = 16384.0
            avail_ram_mb = max(512.0, 16384.0 - rss_now)
    except Exception:
        pass

    # Detect Linux cgroup CPU quota & scheduler affinity (e.g., Hugging Face Spaces / Docker 2-vCPU container on a 64-core Xeon host)
    cgroup_cpus: Optional[int] = None
    try:
        if hasattr(os, "sched_getaffinity"):
            aff_count = len(os.sched_getaffinity(0))
            if 0 < aff_count < physical:
                cgroup_cpus = aff_count
        if os.path.exists("/sys/fs/cgroup/cpu.max"):
            parts = open("/sys/fs/cgroup/cpu.max", "r", encoding="utf-8").read().strip().split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > 0:
                cg_val = max(1, int(round(int(parts[0]) / int(parts[1]))))
                cgroup_cpus = min(cgroup_cpus, cg_val) if cgroup_cpus else cg_val
        elif os.path.exists("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"):
            q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r", encoding="utf-8").read().strip())
            p = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r", encoding="utf-8").read().strip())
            if q > 0 and p > 0:
                cg_val = max(1, int(round(q / p)))
                cgroup_cpus = min(cgroup_cpus, cg_val) if cgroup_cpus else cg_val
    except Exception:
        pass

    env_threads = os.environ.get("SLM_N_THREADS", "").strip()
    if override_threads and override_threads > 0:
        rec_threads = override_threads
    elif env_threads.isdigit() and int(env_threads) > 0:
        rec_threads = int(env_threads)
    elif cgroup_cpus is not None:
        rec_threads = min(8, max(1, cgroup_cpus))
    else:
        if e_cores > 0 and p_cores >= 4:
            rec_threads = min(8, p_cores + max(1, e_cores // 2))
        else:
            rec_threads = min(8, max(1, physical))

    if cgroup_cpus is not None and cgroup_cpus < physical:
        physical = cgroup_cpus
        logical = min(logical, cgroup_cpus)
        p_cores = cgroup_cpus
        e_cores = 0
    elif rec_threads <= 2 and system == "Linux":
        physical = min(physical, rec_threads)
        logical = min(logical, rec_threads)
        p_cores = physical
        e_cores = 0

    # If running in a 2-vCPU Linux container on a large shared Xeon host (>32 GB host RAM),
    # report the 16 GB container memory ceiling rather than the 128 GB multi-tenant host node
    if system == "Linux" and (rec_threads <= 2 or "xeon" in brand.lower()) and total_ram_mb > 32768.0:
        rss_now = get_process_rss_mb()
        total_ram_mb = 16384.0
        avail_ram_mb = max(512.0, 16384.0 - rss_now)

    bg_threads = max(1, min(2, e_cores if e_cores > 0 else max(1, physical // 4)))

    return CPUTopology(
        arch=arch,
        brand=brand,
        logical_cores=logical,
        physical_cores=physical,
        performance_cores=p_cores,
        efficiency_cores=e_cores,
        simd_features=simd,
        total_ram_mb=total_ram_mb,
        available_ram_mb=avail_ram_mb,
        recommended_inference_threads=rec_threads,
        recommended_background_threads=bg_threads,
    )


_CACHED_TOPOLOGY: Optional[CPUTopology] = None


def configure_cpu_environment(override_threads: Optional[int] = None, pure_cpu: bool = True) -> CPUTopology:
    global _CACHED_TOPOLOGY
    topo = detect_cpu_topology(override_threads)
    _CACHED_TOPOLOGY = topo

    t_str = str(topo.recommended_inference_threads)
    os.environ["OMP_NUM_THREADS"] = t_str
    os.environ["MKL_NUM_THREADS"] = t_str
    os.environ["VECLIB_MAXIMUM_THREADS"] = t_str
    os.environ["OPENBLAS_NUM_THREADS"] = t_str
    os.environ["SLM_N_THREADS"] = t_str
    os.environ["ORT_ENABLE_AVX2"] = "1"
    # Passive/1ms wait avoids burning 100% idle CPU spin while keeping intra-op barriers fast
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    os.environ["KMP_BLOCKTIME"] = "1"
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if pure_cpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    return topo


def get_cpu_topology() -> CPUTopology:
    if _CACHED_TOPOLOGY is None:
        return configure_cpu_environment()
    return _CACHED_TOPOLOGY


def get_process_rss_mb() -> float:
    if psutil is not None:
        try:
            return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass
    try:
        import resource
        import sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round((rss / (1024 * 1024)) if sys.platform == "darwin" else (rss / 1024), 1)
    except Exception:
        return 0.0
