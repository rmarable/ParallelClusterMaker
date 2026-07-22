# ParallelClusterMaker Performance Toolkit

Standards-based HPC benchmarks deployed to the cluster head node by
`create_pcluster.yml` when `enable_hpc_benchmarks=true`.

---

## Deployment

When `enable_hpc_benchmarks=true`, `create_pcluster.yml`:

1. Stages cluster-specific scripts (rendered Jinja2 templates) to a local `stage_dir/` tree, then SCP-deploys to `~/performance/<cluster_name>/<cluster_owner>/slurm/` on the head node
2. Uploads the full performance source tree to `s3://<cluster-bucket>/performance/` so postinstall can self-repair on head node rebuild (S3 → `~/performance/` via `aws s3 sync`)
3. Postinstall installs `matplotlib numpy pandas scipy seaborn` via `pip3` on every head node bootstrap

On teardown, `delete_pcluster.yml` syncs results from `~/performance/<cluster_name>/` to `s3://<cluster-bucket>/performance-results/<cluster_name>/<cluster_serial_number>/` before destroying the cluster.  Each serial number gets its own subdirectory so rebuilds of the same cluster name accumulate rather than overwrite.

---

## Quick start

SSH into the head node first (`./access_cluster.py -N <cluster_name>`), then:

```bash
cd ~/performance
module load openmpi    # or intelmpi — already available on ParallelCluster
./hpc-benchmark.sh install
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg
./hpc-benchmark.sh report
```

---

## Standards-based benchmarks (`hpc-benchmark.sh`)

Industry-standard tools that measure what real HPC workloads actually stress:
memory bandwidth, MPI communication, parallel I/O, and sparse linear algebra.

| Benchmark | Measures |
|---|---|
| [STREAM](https://www.cs.virginia.edu/stream/) | Sustainable memory bandwidth — Copy, Scale, Add, Triad |
| [OSU Micro-Benchmarks](https://mvapich.cse.ohio-state.edu/benchmarks/) | MPI point-to-point latency & bandwidth; allreduce and alltoall |
| [IOR](https://github.com/hpc/ior) | Parallel filesystem I/O throughput (POSIX, file-per-process) |
| [HPCG](https://hpcg-benchmark.org/) | Sparse conjugate gradient — more representative of real workloads than HPL |

### Prerequisites

```bash
# MPI (OpenMPI is available by default on ParallelCluster)
module load openmpi    # or: module load intelmpi

# Build tools
sudo apt-get install gcc make wget    # Ubuntu/Debian
sudo dnf install gcc make wget        # RHEL/Amazon Linux
```

### Step 1 — install (one-time, ~5 min)

```bash
./hpc-benchmark.sh install
```

Builds STREAM, OSU v7.4, IOR v4.0.0, and HPCG v3.1 from source into `./bin/`.
To install a subset:

```bash
./hpc-benchmark.sh install --tools stream,osu
```

### Step 2 — run

```bash
# Full suite (30–120 min depending on cluster size)
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg

# Quick memory + MPI check only (~5 min)
./hpc-benchmark.sh run --tests stream,osu

# Parallel I/O against a shared filesystem
./hpc-benchmark.sh run --tests ior --fs-path /fsx/scratch --nodes 4 --ppn 4

# HPCG scaling study across 8 nodes
./hpc-benchmark.sh run --tests hpcg --nodes 8 --ppn 4 --hpcg-time 1800
```

Key options:

| Flag | Default | Notes |
|---|---|---|
| `--tests` | `stream,osu,ior,hpcg` | Comma-separated subset |
| `--nodes` | auto (1 node, all cores) | Nodes for OSU collective, IOR, HPCG |
| `--ppn` | `1` | MPI ranks per node |
| `--fs-path` | `./ior_scratch` | Filesystem to stress with IOR |
| `--ior-size` | `1g` | Per-process transfer size for IOR |
| `--hpcg-time` | `1800` | Min HPCG run time (< 1800 s is flagged invalid) |

STREAM always runs single-node — it measures per-node memory bandwidth.

### Step 3 — report

```bash
./hpc-benchmark.sh report

# Report on a specific run
./hpc-benchmark.sh report --results-dir ./benchmark_results/20260719_143022
```

Results are written to `benchmark_results/<timestamp>/`:

```
stream.txt
osu/latency.txt  osu/bandwidth.txt  osu/allreduce.txt  osu/alltoall.txt
ior.txt
hpcg/hpcg_output.txt  hpcg/HPCG-Benchmark_*.txt
```

### What to look for

**STREAM Triad** is the headline number — it reflects sustainable memory
bandwidth under a real access pattern. Compare against the instance spec sheet
(`lshw -class memory` shows theoretical peak).

**OSU latency at 8 bytes** reveals the raw MPI overhead between two ranks.
On EFA-enabled instances (e.g. `hpc6a`, `c5n`) expect < 2 µs. On standard
Ethernet expect 20–50 µs.

**IOR write/read** shows filesystem throughput. Run it against each filesystem
you plan to use (EBS, EFS, FSx for Lustre) with the same parameters to compare
directly.

**HPCG GFLOP/s** correlates with real solver performance better than HPL because
it exercises sparse, memory-bound operations. Runs under 1800 s are marked
`INVALID` in the official result file.

---

## File inventory

### Top-level (entry points)

| File | Purpose |
|---|---|
| `hpc-benchmark.sh` | Dispatcher: `install`, `run`, `report` |

### `scripts/` (implementation)

No helper scripts remain — `hpc-benchmark.sh` is self-contained and manages its own build and results directories.

### `jinja2/` (cluster-specific templates)

No Jinja2 templates remain for the benchmark suite.

---

## Tips

- Use `byobu` or `screen` on the head node to protect long runs from SSH timeouts.
- The default ParallelCluster autoscaling cap is 5 compute nodes — adjust `max_count` before submitting large job arrays.
- Default AWS EC2 on-demand/spot limits are 20 instances per AZ.
- For IOR against FSx for Lustre, set `--fs-path /fsx` and tune `--ior-size` to match your expected file sizes.
- HPCG problem size (104³ per rank) targets ~25% memory on a 16 GB node. Increase to 128³ or 144³ on larger instances for a more demanding run.
