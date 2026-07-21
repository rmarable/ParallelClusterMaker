#!/bin/bash
set -euo pipefail
################################################################################
# Name:         hpc-perftest.sh
# Author:       Rodney Marable <rodney.marable@gmail.com>
# Created On:   July 19, 2026
# Purpose:      Top-level dispatcher for the ParallelClusterMaker HPC
#               performance testing suite
################################################################################
#
# Usage:
#   ./hpc-perftest.sh <command> [options]
#
# Commands:
#   run     Run Axb_random matrix tests (single-node smoke test)
#   clean   Remove all test artifacts
#   plot    Generate PNG plots from existing CSV data
#   submit  Generate and submit Slurm job arrays
#
# For standards-based MPI/memory/I/O benchmarks use hpc-benchmark.sh.
# Run 'hpc-perftest.sh <command> --help' for per-command options.
################################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"
PYTHON3=python3

# ============================================================================
# Helpers
# ============================================================================

_die() { echo "ERROR: $*" >&2; exit 1; }

_require_parallel() {
    if ! command -v parallel >/dev/null 2>&1; then
        echo "WARNING: GNU parallel not found — falling back to serial mode." >&2
        echo "serial"
    else
        echo "parallel"
    fi
}

_usage_run() {
    cat <<EOF
Usage: hpc-perftest.sh run [options]

  -m, --mode MODE       Execution mode: serial | parallel  (default: serial)
  -n, --count N         Number of jobs to run              (default: 1)
  -C, --cluster NAME    Cluster/instance name for CSV labelling
  -J, --jobid N         Starting job ID; auto-incremented  (default: 1)
  -h, --help            Show this help

For MPI/memory/I/O benchmarks use hpc-benchmark.sh instead.
EOF
}

_usage_clean() {
    cat <<EOF
Usage: hpc-perftest.sh clean [options]

  -y, --yes     Skip confirmation prompt
  -h, --help    Show this help
EOF
}

_usage_plot() {
    cat <<EOF
Usage: hpc-perftest.sh plot [options]

  -T, --type TYPE   Plot type: unified | compute | fileproc | separated | cost
                    (default: unified)
  -C, --cluster NAME  Filter to a specific cluster name (optional)
  -h, --help        Show this help
EOF
}

_usage_submit() {
    cat <<EOF
Usage: hpc-perftest.sh submit [options]

  --start N     Starting task count  (default: 10)
  --step  N     Step size            (default: 10)
  --total N     Total number of steps (default: 10)
  -h, --help    Show this help
EOF
}

_usage_main() {
    cat <<EOF
Usage: hpc-perftest.sh <command> [options]

Commands:
  run     Run Axb_random matrix tests (single-node, no MPI required)
  clean   Remove all test artifacts
  plot    Generate plots from existing CSV data
  submit  Generate and submit Slurm job arrays

Run 'hpc-perftest.sh <command> --help' for details.

Examples:
  hpc-perftest.sh run -n 5 -C my-cluster
  hpc-perftest.sh run -m parallel -n 4 -C my-cluster
  hpc-perftest.sh submit --start 10 --step 10 --total 10
  hpc-perftest.sh plot --type unified
  hpc-perftest.sh clean

For MPI latency/bandwidth, memory bandwidth, parallel I/O, and HPCG:
  hpc-benchmark.sh install
  hpc-benchmark.sh run --tests stream,osu,ior,hpcg
EOF
}

# ============================================================================
# Command: run
# ============================================================================

cmd_run() {
    local mode="serial"
    local count=1
    local cluster
    cluster="$(hostname -s)"
    local jobid=1

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -m|--mode)    [[ $# -ge 2 ]] || _die "$1 requires an argument"; mode="$2";    shift 2 ;;
            -n|--count)   [[ $# -ge 2 ]] || _die "$1 requires an argument"; count="$2";   shift 2 ;;
            -C|--cluster) [[ $# -ge 2 ]] || _die "$1 requires an argument"; cluster="$2"; shift 2 ;;
            -J|--jobid)   [[ $# -ge 2 ]] || _die "$1 requires an argument"; jobid="$2";   shift 2 ;;
            -h|--help)    _usage_run; exit 0 ;;
            *) _die "unknown option: $1. Run 'hpc-perftest.sh run --help'." ;;
        esac
    done

    case "$mode" in
        serial) ;;
        parallel) mode=$(_require_parallel) ;;
        *) _die "invalid mode '$mode'. Choose: serial | parallel" ;;
    esac

    echo ""
    echo "================================================================================"
    echo "  hpc-perftest.sh run"
    echo "  mode=$mode  count=$count  cluster=$cluster  starting_jobid=$jobid"
    echo "================================================================================"
    echo ""

    if [[ "$mode" == "parallel" ]]; then
        seq "$jobid" $(( jobid + count - 1 )) | \
            parallel -j0 bash "$SCRIPTS_DIR/run_axb.sh" --jobid {} --cluster "$cluster"
    else
        local i
        for (( i=0; i<count; i++ )); do
            bash "$SCRIPTS_DIR/run_axb.sh" --jobid $(( jobid + i )) --cluster "$cluster"
        done
    fi

    echo ""
    echo "Combining summary CSVs for $cluster..."
    bash "$SCRIPTS_DIR/combine_csv_summary_files_for_plotting.sh" "$cluster"
    echo ""
    echo "All done. Run 'hpc-perftest.sh plot' to generate graphs."
    echo ""
}

# ============================================================================
# Command: clean
# ============================================================================

cmd_clean() {
    local yes=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -y|--yes) yes=true; shift ;;
            -h|--help) _usage_clean; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    echo ""
    echo "================================================================================"
    echo "  This will DESTROY all performance log and data files in:"
    echo "  $(pwd)"
    echo "================================================================================"
    echo ""

    if [[ "$yes" != true ]]; then
        read -rp "  Type YES to confirm: " confirm
        [[ "$confirm" == "YES" || "$confirm" == "yes" ]] || {
            echo "Aborted."
            exit 1
        }
    fi

    for dir in csv logs plots summary summary_final; do
        if [[ -d "$dir" ]]; then
            echo "Removing $dir/..."
            rm -rf "$dir"
        fi
    done

    for pattern in \
        "job_Axb_random.o*" \
        "job_Axb_random.[0-9]*.csv" \
        "job_Axb_random.[0-9]*.log" \
        "job_Axb_random.[0-9]*.log.gz" \
        "slurm_Axb_random.job*.out" \
        "slurm_Axb_random.job*.err" \
        "[0-9]*.csv" "[0-9]*.log" "[0-9]*.log.gz"
    do
        for f in $pattern; do
            [[ -f "$f" ]] && { echo "Removing $f..."; rm -f "$f"; }
        done
    done

    echo ""
    echo "Clean complete."
    exit 0
}

# ============================================================================
# Command: plot
# ============================================================================

cmd_plot() {
    local plot_type="unified"
    local cluster_arg=()

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -T|--type)    [[ $# -ge 2 ]] || _die "$1 requires an argument"; plot_type="$2"; shift 2 ;;
            -C|--cluster) [[ $# -ge 2 ]] || _die "$1 requires an argument"; cluster_arg=(--cluster "$2"); shift 2 ;;
            -h|--help)    _usage_plot; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    case "$plot_type" in
        unified|compute|fileproc|separated|cost) ;;
        *) _die "invalid plot type '$plot_type'. Choose: unified|compute|fileproc|separated|cost" ;;
    esac

    echo "Generating $plot_type plot..."
    (cd "$SCRIPT_DIR" && $PYTHON3 "$SCRIPTS_DIR/make_standalone_plots.py" --plot "$plot_type" "${cluster_arg[@]}")
}

# ============================================================================
# Command: submit
# ============================================================================

cmd_submit() {
    local start=10
    local step=10
    local total=10

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --start) [[ $# -ge 2 ]] || _die "$1 requires an argument"; start="$2"; shift 2 ;;
            --step)  [[ $# -ge 2 ]] || _die "$1 requires an argument"; step="$2";  shift 2 ;;
            --total) [[ $# -ge 2 ]] || _die "$1 requires an argument"; total="$2"; shift 2 ;;
            -h|--help) _usage_submit; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    if ! command -v sbatch >/dev/null 2>&1; then
        echo "ERROR: sbatch not found. The submit command requires a Slurm environment." >&2
        exit 1
    fi

    echo "Generating sbatch scripts (start=$start, step=$step, total=$total)..."
    (cd "$SCRIPT_DIR" && bash "$SCRIPTS_DIR/generate_sbatch_custom_templates.sh" "$start" "$step" "$total")

    echo "Submitting to Slurm..."
    (cd "$SCRIPT_DIR" && bash "$SCRIPTS_DIR/perf-sbatch.sh")

    echo "Done."
    exit 0
}

# ============================================================================
# Dispatch
# ============================================================================

[[ $# -eq 0 ]] && { _usage_main; exit 1; }

command="$1"; shift
case "$command" in
    run)    cmd_run    "$@" ;;
    clean)  cmd_clean  "$@" ;;
    plot)   cmd_plot   "$@" ;;
    submit) cmd_submit "$@" ;;
    -h|--help) _usage_main; exit 0 ;;
    *) _die "unknown command '$command'. Run 'hpc-perftest.sh --help'." ;;
esac
