# ParallelClusterMaker Performance Toolkit

Two-tier benchmarking suite for HPC clusters and EC2 instances. Deployed to the
cluster head node by `create_pcluster.yml` when `enable_hpc_performance_tests=true`.

---

## Quick start

**Tier 1 — Axb_random smoke test** (no MPI required):
```bash
./hpc-perftest.sh run -n 3 -C my-cluster
./hpc-perftest.sh plot --type unified
```

**Tier 2 — Standards-based benchmarks** (MPI required):
```bash
module load openmpi    # or intelmpi — already available on ParallelCluster
./hpc-benchmark.sh install
./hpc-benchmark.sh run --tests stream,osu,ior,hpcg
./hpc-benchmark.sh report
```

---

## Tier 1 — Axb_random smoke test (`hpc-perftest.sh`)

Solves Ax=b for random normal matrices. No MPI required. Good for a quick
apples-to-apples comparison of raw compute + I/O throughput across instance
types and cluster configurations.

`scripts/run_axb.sh` is the core worker. It invokes `scripts/Axb_random.py`
for each value in `MATRIX_SIZES.conf`, compresses the resulting log with `pigz`
or `gzip`, pastes the timing CSV, and aggregates everything into a summary file.
Use `hpc-perftest.sh run` to drive jobs — you should rarely need to call
`run_axb.sh` directly.

### Prerequisites

```bash
pip install numpy scipy matplotlib pandas seaborn

# Optional: faster log compression
sudo apt-get install pigz        # Ubuntu/Debian
sudo dnf install pigz            # RHEL/Amazon Linux

# Optional: parallel job dispatch
sudo apt-get install parallel
```

### Usage

```bash
# Single run
./hpc-perftest.sh run -C my-cluster

# Five serial runs
./hpc-perftest.sh run -n 5 -C my-cluster

# Four parallel runs (requires GNU parallel)
./hpc-perftest.sh run -m parallel -n 4 -C my-cluster

# Plot results
./hpc-perftest.sh plot --type unified

# Clean up all artifacts
./hpc-perftest.sh clean
./hpc-perftest.sh clean --yes    # skip confirmation prompt
```

### Matrix sizes

Edit `MATRIX_SIZES.conf` before running. The default (`1000 2000 3000 4000 5000`)
completes in ~10 minutes on an m5.2xlarge.

| Profile | `MATRIX_SIZES` value | Notes |
|---|---|---|
| Quick (~5 min) | `500 1000 1500 2000 2500` | Smoke test |
| Default (~10 min) | `1000 2000 3000 4000 5000` | Good baseline |
| Full (~45 min) | `1000 2000 3000 4000 5000 6000 7000 8000 9000 10000` | Full regression |
| Memory-intensive (≥16 GB) | `2500 5000 7500 10000 12500 15000` | Large instance only |
| Scheduler stress | `100 250 500 750 1000 1250 1500 1750 2000 2250 2500` | Many small jobs |

N > 8000 requires at least 16 GB RAM. Log files for a 10000×10000 matrix are ~3 GB — watch disk usage when `--create-logs` is enabled.

### Plot types

```bash
./hpc-perftest.sh plot --type unified     # compute + I/O combined (default)
./hpc-perftest.sh plot --type compute     # CPU time only
./hpc-perftest.sh plot --type fileproc    # I/O time only
./hpc-perftest.sh plot --type separated   # compute vs I/O as separate series
./hpc-perftest.sh plot --type cost        # estimated cost per run
```

PNG files land in `plots/`. matplotlib always uses the non-interactive `Agg`
backend — no display is needed on EC2 or any other headless environment.

### Slurm job array workflow

```bash
# Generate sbatch scripts: task counts 10, 20, ..., 100
./hpc-perftest.sh submit --start 10 --step 10 --total 10

# After jobs complete, combine CSVs and plot
./scripts/combine_csv_summary_files_for_plotting.sh <cluster_name>
./hpc-perftest.sh plot --type unified
```

### Axb_random.py direct usage

```
python3 scripts/Axb_random.py --jobid JOBID --matrix-size N [options]

  --jobid/-J         Job name (prefix for log/CSV filenames)
  --matrix-size/-M   NxN matrix dimension
  --console-dump/-D  Print A, b, x to stdout        (default: yes)
  --create-csv/-C    Write timing data to CSV        (default: yes)
  --create-logs/-L   Write matrix output to log file (default: yes)
  --note/-N          Short label included in CSV (no commas)
```

At least one output mode must be enabled. Enabling `--console-dump` and
`--create-logs` together costs ~2× compared to either alone.

### Performance reference

Measured on a 2017 MacBook Pro (2.5 GHz i7, 16 GB RAM, SSD):

| N | dump+log+csv | dump+csv | log+csv | csv only |
|---|---|---|---|---|
| 512 | 1.71 s | 0.99 s | 0.82 s | 0.17 s |
| 1024 | 9.15 s | 4.85 s | 4.16 s | 0.18 s |
| 2048 | 59.9 s | 30.3 s | 25.9 s | 0.58 s |
| 4096 | 408.8 s | 208.9 s | 186.8 s | 2.16 s |
| 8192 | 2595.6 s | 1330.4 s | 1229.4 s | 10.74 s |

---

## Tier 2 — Standards-based benchmarks (`hpc-benchmark.sh`)

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
| `hpc-perftest.sh` | Dispatcher: `run`, `clean`, `plot`, `submit` |
| `hpc-benchmark.sh` | Dispatcher: `install`, `run`, `report` |
| `MATRIX_SIZES.conf` | Space-separated matrix sizes — edit to configure test scope |

### `scripts/` (implementation)

| File | Purpose |
|---|---|
| `run_axb.sh` | Single Axb_random worker for one job ID |
| `Axb_random.py` | Solve Ax=b; write log and CSV |
| `compress_logfiles.py` | Compress one `.log` file with pigz or gzip |
| `make_standalone_plots.py` | Generate matplotlib PNG plots from `summary_final/*.csv` |
| `combine_csv_summary_files_for_plotting.sh` | Merge per-job CSVs into `summary_final/` |
| `csv_summary_time_measurement.sh` | Aggregate raw per-task CSV rows into per-job summaries |
| `generate_sbatch_custom_templates.sh` | Stamp numbered sbatch scripts from the Axb_random template |
| `perf-sbatch.sh` | Submit all `sbatch-Axb_random.*.sh` scripts to Slurm |

### `jinja2/` (cluster-specific templates)

Rendered by `create_pcluster.yml` at cluster creation time and copied to the
head node with `.<cluster_name>.sh` in the filename.

| Template | Renders to |
|---|---|
| `combine_csv_summary_files_for_plotting.j2` | `combine_csv_summary_files_for_plotting.<cluster_name>.sh` |
| `perf-sbatch.j2` | `perf-sbatch.<cluster_name>.sh` |
| `sbatch_Axb_random_template.j2` | Master; `generate_sbatch_custom_templates.sh` stamps numbered copies |

---

## Tips

- Use `byobu` or `screen` on the head node to protect long runs from SSH timeouts.
- The default ParallelCluster autoscaling cap is 5 compute nodes — adjust `max_count` before submitting large job arrays.
- Default AWS EC2 on-demand/spot limits are 20 instances per AZ.
- For IOR against FSx for Lustre, set `--fs-path /fsx` and tune `--ior-size` to match your expected file sizes.
- HPCG problem size (104³ per rank) targets ~25% memory on a 16 GB node. Increase to 128³ or 144³ on larger instances for a more demanding run.
