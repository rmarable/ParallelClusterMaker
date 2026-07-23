# FOR IMMEDIATE RELEASE

---

# ParallelClusterMaker Modernized to Support AWS ParallelCluster v3

## Bringing Reproducible HPC Clusters to Graviton4 and GPU Workloads

**Cambridge, MA — July 23, 2026** — ParallelClusterMaker, an open-source command-line toolkit for managing AWS ParallelCluster environments, today announced support for newer versions of AWS ParallelCluster, making it easier for HPC engineers to run reproducible, cost-controlled clusters on AWS. ParallelClusterMaker automates the full lifecycle of AWS ParallelCluster environments — create, access, monitor, benchmark, and destroy — from a single CLI.

---

## What's New

### Graviton4 and ARM64 Support

Default instances are migrated from `c5` to `c8g` (Graviton4) with `ubuntu2404arm` as the standard OS, providing better price/performance for many HPC workloads. ARM64 variants are supported for Ubuntu 22.04/24.04 and RHEL 8/9, and the toolkit normalizes OS names before handing them to ParallelCluster configuration to prevent validation failures. Python 3.12 is pinned to ensure compatibility with ParallelCluster up to version 3.15.1.

### Grafana/Prometheus Monitoring

With `--enable_monitoring=true`, users can deploy Grafana dashboards, Prometheus, Slurm exporter, node exporter, CloudWatch exporter, and pushgateway on the head node via Docker Compose. The monitoring artifacts are staged to Amazon S3 at build time so private-subnet and air-gapped clusters never need to reach out to GitHub at boot. Each cluster includes a generated `grafana_tunnel.<cluster>.sh` script that opens a secure SSH tunnel and prints a ready-to-use URL and password retrieval command for Grafana.

### SSH Key Management and IAM Hardening

Cluster SSH private keys are stored in AWS Secrets Manager at creation and deleted on teardown, with per-cluster scripts for key recovery and a `rotate_cluster_key.py` utility that rotates ED25519 keypairs in place without rebuilding the cluster. Head-node instance policies are split into multiple managed policies to stay within IAM size limits, and a potential privilege escalation via `iam:PutRolePolicy` is closed by enforcing permissions boundaries on role creation and policy updates. Ansible verbosity is restricted through an explicit allowlist to prevent argument injection.

### HPC Benchmark Suite

With `--enable_hpc_benchmarks=true`, the toolkit deploys STREAM, OSU Micro-Benchmarks, IOR, and HPCG via postinstall, syncing the suite from S3 at build time and returning results to S3 on teardown. Benchmark outputs are keyed by cluster serial number so multiple rebuilds accumulate historical data, and plots include power-law curve fits to show scaling behavior across matrix sizes.

### GPU Instance Support

When `--enable_gpu=true` is specified — or when the compute instance type is auto-detected as a known GPU family (`g4dn`, `g4ad`, `g5`, `g5g`, `g6`, `gr6`, `p3`, `p3dn`, `p4d`, `p4de`, `p5`) — ParallelClusterMaker installs `nvtop` and `htop`, configures NVMe instance storage under `/local_scratch` using XFS or RAID0, and identifies instance storage devices via kernel sysfs metadata rather than hard-coded paths. For `p4d`, `p4de`, and `p5` instances with EFA enabled, the toolkit automatically sets `GdrSupport: true` in the ParallelCluster configuration, enabling EFA GPUDirect RDMA for high-performance MPI and deep learning workloads.

### Private-Subnet and VPC-Internal Clusters

The `access_cluster.py` helper and Grafana tunnel scripts automatically fall back to the head node's private IP when no public IP is present, so clusters deployed entirely inside a VPC can be accessed without additional routing or security group changes.

### Testing

Over 300 unit tests exercise Python logic, Jinja2 template rendering, and IAM policy validity without requiring AWS credentials. An integration smoke test harness runs full create / Slurm-job / teardown cycles against a live AWS account to validate the complete deployment pipeline.

---

## Quote

> "AWS ParallelCluster is powerful, but spinning up a safe, repeatable cluster with all the right IAM, monitoring, and teardown behavior is still too much work for many teams. This release brings modern ParallelCluster v3 support, Graviton4 defaults, and GPU-aware configuration into one CLI, so HPC engineers can spend more time helping stakeholders get stuff done and waste less time in DevOps hell."
>
> — Rodney Marable, creator of ParallelClusterMaker

---

## Background

When ParallelClusterMaker was first published in 2019, AWS Chief Evangelist Jeff Barr described it as *"a command line toolkit for automating the creation and destruction of AWS ParallelCluster stacks."* The project remains open source and is licensed under MIT with a Commons Clause.

---

## About Claude Code

This modernization was developed collaboratively with [Claude Code](https://claude.ai/code), Anthropic's AI coding assistant. The repository includes a `CLAUDE.md` file describing project context, constraints, and conventions to help contributors get productive quickly when using Claude Code.

---

## Links

| | |
|---|---|
| **Project repo** | https://github.com/rmarable/ParallelClusterMaker |
| **Contact** | Rodney Marable — rodney.marable@gmail.com |
