#!/bin/bash
set -euo pipefail
################################################################################
# Name:         hpc-benchmark.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   July 19, 2026
# Purpose:      Standards-based HPC benchmark suite for ParallelClusterMaker
#               Covers memory bandwidth (STREAM), MPI latency/bandwidth (OSU),
#               parallel I/O (IOR), and sparse linear algebra scaling (HPCG)
################################################################################
#
# Usage:
#   ./hpc-benchmark.sh install [--prefix DIR]
#   ./hpc-benchmark.sh run    --tests stream,osu,ior,hpcg [options]
#   ./hpc-benchmark.sh report [--results-dir DIR]
#
# Run 'hpc-benchmark.sh <command> --help' for per-command options.
#
# Prerequisites on the head node:
#   - MPI available via 'module load' or already on PATH
#     (ParallelCluster provides OpenMPI and IntelMPI by default)
#   - gcc, make, wget/curl
#   - For IOR: HDF5 optional; plain POSIX mode works without it
################################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH_BIN="${SCRIPT_DIR}/bin"
RESULTS_DIR="${SCRIPT_DIR}/benchmark_results"

# Tool versions — update here when newer releases are available
STREAM_URL="https://www.cs.virginia.edu/stream/FTP/Code/stream.c"
OSU_VERSION="7.4"
OSU_URL="https://mvapich.cse.ohio-state.edu/download/mvapich/osu-micro-benchmarks-${OSU_VERSION}.tar.gz"
IOR_VERSION="4.0.0"
IOR_URL="https://github.com/hpc/ior/releases/download/${IOR_VERSION}/ior-${IOR_VERSION}.tar.gz"
HPCG_VERSION="3.1"
HPCG_URL="https://github.com/hpcg-benchmark/hpcg/archive/refs/tags/HPCG-release-${HPCG_VERSION}_2019-11-05.tar.gz"

# ============================================================================
# Helpers
# ============================================================================

_die()  { printf "ERROR: %s\n" "$*" >&2; exit 1; }
_info() { echo "==> $*"; }

_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || _die "'$1' not found on PATH. Install it or load the appropriate module."
}

_detect_mpi() {
    if command -v mpirun >/dev/null 2>&1; then
        echo "mpirun"
    elif command -v mpiexec >/dev/null 2>&1; then
        echo "mpiexec"
    else
        _die $'No MPI launcher found (mpirun/mpiexec). Load an MPI module first:\n  module load openmpi  or  module load intelmpi'
    fi
}

_nproc_all() {
    # Total MPI ranks across all available slots (hostfile-aware if SLURM_NTASKS set)
    if [[ -n "${SLURM_NTASKS:-}" ]]; then
        echo "$SLURM_NTASKS"
    else
        nproc
    fi
}

_timestamp() { date +%Y%m%d_%H%M%S; }

_usage_install() {
    cat <<EOF
Usage: hpc-benchmark.sh install [options]

  --prefix DIR    Install binaries under DIR (default: ./bin)
  --tools LIST    Comma-separated subset to build: stream,osu,ior,hpcg
                  (default: all)
  -h, --help      Show this help

Builds from source. Requires: gcc, make, wget or curl, MPI on PATH.
EOF
}

_usage_run() {
    cat <<EOF
Usage: hpc-benchmark.sh run [options]

  --tests LIST    Comma-separated tests to run: stream,osu,ior,hpcg
                  (default: stream,osu,ior,hpcg)
  --nodes N       Number of MPI nodes/ranks to use (default: auto-detect)
  --ppn N         MPI processes per node (default: 1 for latency tests)
  --fs-path DIR   Filesystem path for IOR test (default: ./ior_scratch)
  --ior-size STR  IOR per-process transfer size (default: 1g)
  --hpcg-time N   HPCG minimum run time in seconds (default: 1800)
  --results-dir D Write results to DIR (default: ./benchmark_results)
  -h, --help      Show this help

The --nodes flag controls OSU, IOR, and HPCG parallelism.
STREAM always runs single-node (it measures per-node memory bandwidth).
EOF
}

_usage_report() {
    cat <<EOF
Usage: hpc-benchmark.sh report [options]

  --results-dir DIR   Read results from DIR (default: ./benchmark_results)
  -h, --help          Show this help
EOF
}

_usage_main() {
    cat <<EOF
Usage: hpc-benchmark.sh <command> [options]

Commands:
  install   Download and build benchmark tools
  run       Execute benchmarks and save results
  report    Summarise results from a previous run

Run 'hpc-benchmark.sh <command> --help' for per-command details.

Examples:
  # First time: build everything
  hpc-benchmark.sh install

  # Full suite (30-120 min depending on cluster size)
  hpc-benchmark.sh run --tests stream,osu,ior,hpcg

  # Quick memory + MPI check only (5-10 min)
  hpc-benchmark.sh run --tests stream,osu

  # Parallel I/O test against a shared filesystem
  hpc-benchmark.sh run --tests ior --fs-path /fsx/scratch --nodes 4

  # Full HPCG scaling run
  hpc-benchmark.sh run --tests hpcg --nodes 8 --ppn 4 --hpcg-time 1800

  # Summarise last run
  hpc-benchmark.sh report
EOF
}

# ============================================================================
# Command: install
# ============================================================================

cmd_install() {
    local prefix="$BENCH_BIN"
    local tools="stream,osu,ior,hpcg"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --prefix) prefix="$2"; shift 2 ;;
            --tools)  tools="$2";  shift 2 ;;
            -h|--help) _usage_install; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    _require_cmd gcc
    _require_cmd make
    if command -v wget >/dev/null 2>&1; then
        _fetch() { wget -q -O "$1" "$2"; }
    else
        _fetch() { curl -sL -o "$1" "$2"; }
    fi

    mkdir -p "$prefix"
    _build_tmpdir=$(mktemp -d /tmp/hpc-benchmark-build.XXXXXX)
    trap 'rm -rf "$_build_tmpdir"' EXIT

    IFS=',' read -ra TOOL_LIST <<< "$tools"
    for tool in "${TOOL_LIST[@]}"; do
        case "$tool" in

        # ------------------------------------------------------------------ #
        # STREAM — single-node memory bandwidth                               #
        # ------------------------------------------------------------------ #
        stream)
            _info "Building STREAM..."
            _fetch "$_build_tmpdir/stream.c" "$STREAM_URL"
            # STREAM_ARRAY_SIZE: at least 4x L3 cache; 80M elements ~= 600 MB
            # NTIMES=20 gives stable median; -O3 -march=native for real bandwidth
            gcc -O3 -march=native -fopenmp \
                -DSTREAM_ARRAY_SIZE=80000000 -DNTIMES=20 \
                -o "$prefix/stream" "$_build_tmpdir/stream.c"
            _info "STREAM installed at $prefix/stream"
            ;;

        # ------------------------------------------------------------------ #
        # OSU Micro-Benchmarks — MPI point-to-point and collective            #
        # ------------------------------------------------------------------ #
        osu)
            _detect_mpi >/dev/null
            _info "Building OSU Micro-Benchmarks ${OSU_VERSION}..."
            _fetch "$_build_tmpdir/osu.tar.gz" "$OSU_URL"
            tar -xzf "$_build_tmpdir/osu.tar.gz" -C "$_build_tmpdir"
            local osu_src="$_build_tmpdir/osu-micro-benchmarks-${OSU_VERSION}"
            pushd "$osu_src" >/dev/null
            ./configure --prefix="$prefix/osu" CC=mpicc CXX=mpicxx \
                --enable-cuda=no >/dev/null 2>&1
            make -j"$(nproc)" install >/dev/null 2>&1
            popd >/dev/null
            _info "OSU installed at $prefix/osu/"
            ;;

        # ------------------------------------------------------------------ #
        # IOR — parallel I/O                                                  #
        # ------------------------------------------------------------------ #
        ior)
            _detect_mpi >/dev/null
            _info "Building IOR ${IOR_VERSION}..."
            _fetch "$_build_tmpdir/ior.tar.gz" "$IOR_URL"
            tar -xzf "$_build_tmpdir/ior.tar.gz" -C "$_build_tmpdir"
            local ior_src="$_build_tmpdir/ior-${IOR_VERSION}"
            pushd "$ior_src" >/dev/null
            ./configure --prefix="$prefix/ior" CC=mpicc >/dev/null 2>&1
            make -j"$(nproc)" install >/dev/null 2>&1
            popd >/dev/null
            _info "IOR installed at $prefix/ior/"
            ;;

        # ------------------------------------------------------------------ #
        # HPCG — sparse CG solver scaling                                     #
        # ------------------------------------------------------------------ #
        hpcg)
            _detect_mpi >/dev/null
            _info "Building HPCG ${HPCG_VERSION}..."
            _fetch "$_build_tmpdir/hpcg.tar.gz" "$HPCG_URL"
            tar -xzf "$_build_tmpdir/hpcg.tar.gz" -C "$_build_tmpdir"
            local hpcg_src
            hpcg_src=$(find "$_build_tmpdir" -maxdepth 1 -type d -name 'hpcg-*' | sort | head -1)
            [[ -d "$hpcg_src" ]] || _die "HPCG source directory not found after extract"
            mkdir -p "$hpcg_src/build"
            pushd "$hpcg_src/build" >/dev/null
            # Use the MPI_GCC setup; HPCG ships its own build system
            ../configure MPI_GCC >/dev/null 2>&1
            make -j"$(nproc)" >/dev/null 2>&1
            mkdir -p "$prefix/hpcg/bin"
            cp bin/xhpcg "$prefix/hpcg/bin/"
            cp "$hpcg_src/testing/hpcg.dat" "$prefix/hpcg/"
            popd >/dev/null
            _info "HPCG installed at $prefix/hpcg/"
            ;;

        *) _die "unknown tool '$tool'. Choose from: stream,osu,ior,hpcg" ;;
        esac
    done

    _info "Install complete. Run 'hpc-benchmark.sh run' to execute benchmarks."
}

# ============================================================================
# Command: run
# ============================================================================

cmd_run() {
    local tests="stream,osu,ior,hpcg"
    local nodes=""
    local ppn=1
    local fs_path="${SCRIPT_DIR}/ior_scratch"
    local ior_size="1g"
    local hpcg_time=1800
    local results_dir="$RESULTS_DIR"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tests)       [[ $# -ge 2 ]] || _die "$1 requires an argument"; tests="$2";       shift 2 ;;
            --nodes)       [[ $# -ge 2 ]] || _die "$1 requires an argument"; nodes="$2";       shift 2 ;;
            --ppn)         [[ $# -ge 2 ]] || _die "$1 requires an argument"; ppn="$2";         shift 2 ;;
            --fs-path)     [[ $# -ge 2 ]] || _die "$1 requires an argument"; fs_path="$2";     shift 2 ;;
            --ior-size)    [[ $# -ge 2 ]] || _die "$1 requires an argument"; ior_size="$2";    shift 2 ;;
            --hpcg-time)   [[ $# -ge 2 ]] || _die "$1 requires an argument"; hpcg_time="$2";   shift 2 ;;
            --results-dir) [[ $# -ge 2 ]] || _die "$1 requires an argument"; results_dir="$2"; shift 2 ;;
            -h|--help) _usage_run; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    local mpi_launcher
    mpi_launcher=$(_detect_mpi)

    # Multi-node runs must be inside a Slurm allocation.
    if [[ -n "$nodes" && "$nodes" -gt 1 && -z "${SLURM_JOB_ID:-}" ]]; then
        _die "Multi-node runs require a Slurm allocation. Submit via sbatch or run interactively with:
  srun --nodes=$nodes --ntasks-per-node=$ppn --pty bash
  ./hpc-benchmark.sh run --tests $tests --nodes $nodes --ppn $ppn"
    fi

    local total_ranks
    if [[ -n "$nodes" ]]; then
        total_ranks=$(( nodes * ppn ))
    else
        # Auto-detect: use all available processors across 1 logical node.
        # Pass --nodes and --ppn explicitly on multi-node SLURM jobs.
        nodes=$(_nproc_all)
        ppn=1
        total_ranks=$nodes
    fi

    local ts
    ts=$(_timestamp)
    mkdir -p "$results_dir/$ts"

    # Record the invocation so report can display it later.
    echo "./hpc-benchmark.sh run --tests $tests --nodes $nodes --ppn $ppn --hpcg-time $hpcg_time --ior-size $ior_size --results-dir $results_dir" \
        > "$results_dir/$ts/cmd.txt"

    echo ""
    echo "================================================================================"
    echo "  hpc-benchmark.sh run"
    echo "  tests=$tests  nodes=$nodes  ppn=$ppn  timestamp=$ts"
    echo "  results -> $results_dir/$ts"
    echo "================================================================================"
    echo ""

    IFS=',' read -ra TEST_LIST <<< "$tests"
    for test in "${TEST_LIST[@]}"; do
        case "$test" in

        # ------------------------------------------------------------------ #
        # STREAM                                                               #
        # ------------------------------------------------------------------ #
        stream)
            local stream_bin="$BENCH_BIN/stream"
            [[ -x "$stream_bin" ]] || _die "STREAM not found at $stream_bin. Run 'hpc-benchmark.sh install --tools stream' first."
            _info "Running STREAM (single node, $(nproc) threads)..."
            OMP_NUM_THREADS=$(nproc) "$stream_bin" \
                | tee "$results_dir/$ts/stream.txt"
            _info "STREAM results -> $results_dir/$ts/stream.txt"
            ;;

        # ------------------------------------------------------------------ #
        # OSU                                                                  #
        # ------------------------------------------------------------------ #
        osu)
            local osu_pt2pt="$BENCH_BIN/osu/libexec/osu-micro-benchmarks/mpi/pt2pt"
            local osu_coll="$BENCH_BIN/osu/libexec/osu-micro-benchmarks/mpi/collective"
            [[ -d "$osu_pt2pt" ]] || _die "OSU not found at $BENCH_BIN/osu/. Run 'hpc-benchmark.sh install --tools osu' first."

            mkdir -p "$results_dir/$ts/osu"

            _info "Running OSU latency (2 ranks)..."
            $mpi_launcher -n 2 "$osu_pt2pt/osu_latency" \
                | tee "$results_dir/$ts/osu/latency.txt"

            _info "Running OSU bandwidth (2 ranks)..."
            $mpi_launcher -n 2 "$osu_pt2pt/osu_bw" \
                | tee "$results_dir/$ts/osu/bandwidth.txt"

            _info "Running OSU all-reduce (${total_ranks} ranks)..."
            $mpi_launcher -n "$total_ranks" "$osu_coll/osu_allreduce" \
                | tee "$results_dir/$ts/osu/allreduce.txt"

            _info "Running OSU all-to-all (${total_ranks} ranks)..."
            $mpi_launcher -n "$total_ranks" "$osu_coll/osu_alltoall" \
                | tee "$results_dir/$ts/osu/alltoall.txt"

            _info "OSU results -> $results_dir/$ts/osu/"
            ;;

        # ------------------------------------------------------------------ #
        # IOR                                                                  #
        # ------------------------------------------------------------------ #
        ior)
            local ior_bin="$BENCH_BIN/ior/bin/ior"
            [[ -x "$ior_bin" ]] || _die "IOR not found at $ior_bin. Run 'hpc-benchmark.sh install --tools ior' first."
            mkdir -p "$fs_path"

            _info "Running IOR (${total_ranks} ranks, ${ior_size}/process, path: ${fs_path})..."
            # Write then read; -F = file-per-process; -C = reorder tasks to avoid cache
            $mpi_launcher -n "$total_ranks" "$ior_bin" \
                -a POSIX -F -w -r -C \
                -t 1m -b "$ior_size" \
                -o "$fs_path/ior_testfile" \
                -v \
                | tee "$results_dir/$ts/ior.txt"

            rm -f "$fs_path/ior_testfile"*
            _info "IOR results -> $results_dir/$ts/ior.txt"
            ;;

        # ------------------------------------------------------------------ #
        # HPCG                                                                 #
        # ------------------------------------------------------------------ #
        hpcg)
            local hpcg_bin="$BENCH_BIN/hpcg/bin/xhpcg"
            [[ -x "$hpcg_bin" ]] || _die "HPCG not found at $hpcg_bin. Run 'hpc-benchmark.sh install --tools hpcg' first."

            local hpcg_run="$results_dir/$ts/hpcg"
            mkdir -p "$hpcg_run"

            # HPCG problem size: 104^3 per MPI rank is a reasonable default;
            # increase for larger memory nodes (use ~25% of available RAM per rank).
            # Run time < 1800s is flagged as invalid in the official results.
            cat > "$hpcg_run/hpcg.dat" <<EOF
HPCG benchmark input file
Sandia National Laboratories; University of Tennessee, Knoxville
104 104 104
$hpcg_time
EOF
            _info "Running HPCG (${total_ranks} ranks, min ${hpcg_time}s run)..."
            pushd "$hpcg_run" >/dev/null
            $mpi_launcher -n "$total_ranks" "$hpcg_bin" \
                | tee hpcg_output.txt
            popd >/dev/null

            # HPCG writes its own HPCG-Benchmark_*.txt result file
            _info "HPCG results -> $hpcg_run/"
            ;;

        *) _die "unknown test '$test'. Choose from: stream,osu,ior,hpcg" ;;
        esac
    done

    echo ""
    _info "All benchmarks complete. Results in $results_dir/$ts/"
    echo ""
    echo "Run 'hpc-benchmark.sh report --results-dir $results_dir/$ts' for a summary."
    echo ""
}

# ============================================================================
# Command: report
# ============================================================================

cmd_report() {
    local results_dir="$RESULTS_DIR"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --results-dir) results_dir="$2"; shift 2 ;;
            -h|--help) _usage_report; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    # If passed the parent dir, use the most recent timestamped run
    if [[ ! -f "$results_dir/stream.txt" && ! -d "$results_dir/osu" ]]; then
        local latest
        latest=$(find "$results_dir" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*' | sort -r | head -1)
        [[ -n "$latest" ]] || _die "No results found in $results_dir"
        results_dir="$latest"
    fi

    echo ""
    echo "================================================================================"
    echo "  hpc-benchmark.sh report"
    echo "  Results from: $results_dir"
    echo "================================================================================"

    if [[ -f "$results_dir/cmd.txt" ]]; then
        echo ""
        echo "--- Command ---"
        echo "  $(cat "$results_dir/cmd.txt")"
    fi

    # ---- STREAM ----
    if [[ -f "$results_dir/stream.txt" ]]; then
        echo ""
        echo "--- STREAM (memory bandwidth) ---"
        grep -E "^(Copy|Scale|Add|Triad):" "$results_dir/stream.txt" \
            | awk '{printf "  %-8s %s MB/s\n", $1, $2}' || true
    fi

    # ---- OSU latency/bandwidth ----
    if [[ -f "$results_dir/osu/latency.txt" ]]; then
        echo ""
        echo "--- OSU MPI Latency (pt2pt, 8-byte message) ---"
        awk 'NF==2 && $1=="8" {printf "  %s bytes -> %s us\n", $1, $2; exit}' \
            "$results_dir/osu/latency.txt" || true
        echo "--- OSU MPI Bandwidth (pt2pt, peak) ---"
        awk 'NF==2 && $2+0>0 {max=$2; msg=$1} END {printf "  %s bytes -> %s MB/s\n", msg, max}' \
            "$results_dir/osu/bandwidth.txt" 2>/dev/null || true
    fi

    # ---- IOR ----
    if [[ -f "$results_dir/ior.txt" ]]; then
        echo ""
        echo "--- IOR (parallel I/O) ---"
        grep -E "^(write|read)" "$results_dir/ior.txt" \
            | awk '{printf "  %-6s %s MB/s\n", $1, $2}' || true
    fi

    # ---- HPCG ----
    local hpcg_result
    hpcg_result=$(find "$results_dir/hpcg" -maxdepth 1 -name 'HPCG-Benchmark_*.txt' 2>/dev/null | sort | head -1)
    if [[ -f "$hpcg_result" ]]; then
        echo ""
        echo "--- HPCG (sparse CG scaling) ---"
        grep "GFLOP/s rating" "$hpcg_result" \
            | awk -F= '{printf "  %s\n", $2}' || true
    fi

    echo ""
    echo "Full output files:"
    find "$results_dir" -type f | sort | sed 's/^/  /'
    echo ""
}

# ============================================================================
# Dispatch
# ============================================================================

[[ $# -eq 0 ]] && { _usage_main; exit 1; }

command="$1"; shift
case "$command" in
    install) cmd_install "$@" ;;
    run)     cmd_run     "$@" ;;
    report)  cmd_report  "$@" ;;
    -h|--help) _usage_main; exit 0 ;;
    *) _die "unknown command '$command'. Run 'hpc-benchmark.sh --help'." ;;
esac
