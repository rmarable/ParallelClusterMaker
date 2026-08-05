#!/bin/bash
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

# `dirname -- "$0"`, not `dirname "$0"`: when this file is sourced from an
# interactive login shell $0 is `-bash`, which dirname parses as a bundle of
# short options and rejects with `invalid option -- 'b'`.  The library guard
# below cannot help -- it sits above the dispatch block, hundreds of lines after
# this line has already run.  Sourcing is a supported entry point
# (HPC_BENCHMARK_LIB_ONLY), so this must not depend on $0 naming a real path;
# with `--` dirname answers `.` for any such argv[0] and SCRIPT_DIR becomes $PWD.
SCRIPT_DIR="$(cd "$(dirname -- "$0")" && pwd)"
BENCH_BIN="${SCRIPT_DIR}/bin"
RESULTS_DIR="${SCRIPT_DIR}/benchmark_results"

# Tool versions — update the URL/checksum pair together when bumping a version.
# Obtain a new checksum with: curl -sL <url> | sha256sum
STREAM_URL="https://www.cs.virginia.edu/stream/FTP/Code/stream.c"
STREAM_SHA256="a52bae5e175bea3f7832112af9c085adab47117f7d2ce219165379849231692b"
OSU_VERSION="7.5.2"
OSU_URL="https://mvapich.cse.ohio-state.edu/download/mvapich/osu-micro-benchmarks-${OSU_VERSION}.tar.gz"
OSU_SHA256="618de3d0b1122f73a9229177d2da1e5cd62e431190580cb915f2605849cbbbdc"
IOR_VERSION="4.0.0"
IOR_URL="https://github.com/hpc/ior/releases/download/${IOR_VERSION}/ior-${IOR_VERSION}.tar.gz"
IOR_SHA256="510b7d4ad0f287375848121aa5a1f9842db077c1d81ad0dde738e96255298158"
HPCG_VERSION="3-1-0"
HPCG_URL="https://github.com/hpcg-benchmark/hpcg/archive/refs/tags/HPCG-release-${HPCG_VERSION}.tar.gz"
HPCG_SHA256="be841a30231c09b80d715600846212d2854c6709a56ba85af337e43a26255133"

# ============================================================================
# Helpers
# ============================================================================

_die()  { printf "ERROR: %s\n" "$*" >&2; exit 1; }
_info() { echo "==> $*"; }

_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || _die "'$1' not found on PATH. Install it or load the appropriate module."
}

# _download DEST URL: fetch a URL with whichever downloader is available.
_download() {
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$1" "$2"
    else
        curl -sL -f -o "$1" "$2"
    fi
}

# _fetch DEST URL SHA256: download and verify the file's checksum before
# returning. Aborts on download failure (HTTP error, network error) or a
# checksum mismatch (moved/hijacked/corrupted download) rather than
# silently handing a bad file to the build.
#
# Kept at top level, not nested inside cmd_install, so the test suite can source
# this script and drive it with a stubbed _download against fixture files.
_fetch() {
    local dest="$1" url="$2" expected_sha="$3"
    _download "$dest" "$url" || _die "download failed: $url"
    local actual_sha
    actual_sha=$(sha256sum "$dest" | awk '{print $1}')
    [[ "$actual_sha" == "$expected_sha" ]] || _die \
        "checksum mismatch for $url
  expected: $expected_sha
  actual:   $actual_sha
  The file may have moved, been corrupted, or been tampered with. Aborting."
}

# _build_step LABEL LOGFILE CMD...: run a configure/make step with its output
# captured rather than discarded. On failure, print the tail of the log and the
# path to the whole thing. Silencing these with >/dev/null 2>&1 meant a failed
# build reported only "HPCG build failed" with no way to find out why.
_build_step() { _try_build_step "$@" || exit 1; }

# _try_build_step: same, but returns non-zero instead of exiting. run's optional
# CUDA OSU build uses this -- the host-to-host results have already been written
# by that point, so aborting the whole run over an extra is wrong.
_try_build_step() {
    local label="$1" logfile="$2"
    shift 2
    if ! "$@" >>"$logfile" 2>&1; then
        echo "ERROR: $label" >&2
        echo "--- last 40 lines of $logfile ---" >&2
        tail -40 "$logfile" >&2
        echo "--- full log: $logfile ---" >&2
        return 1
    fi
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

# Virtual interfaces that carry the SAME address on every node, so a peer
# address drawn from one routes to the local host instead of the peer. docker0
# is 172.17.0.1/16 on all of them, which is what --enable_monitoring puts there.
_VIRTUAL_IFACE_GLOBS='docker* br-* virbr* veth* cni* flannel* cali* tunl* nerdctl*'

# Interfaces present on this node that must never be used for MPI.
#
# Runtime probe, not a fixed list: hpc-benchmark.sh is copy:d rather than
# rendered, and which bridges exist depends on what the node booted with.
#
# HPC_BENCHMARK_NET_DIR is a test seam. macOS has no /sys/class/net at all, so
# on a developer's machine this function returns empty no matter what is wrong
# with it -- the suite would pass against any implementation. It is never set
# on a node.
_virtual_ifaces() {
    local netdir="${HPC_BENCHMARK_NET_DIR:-/sys/class/net}"
    local found=() iface
    for iface in "$netdir"/*; do
        [[ -e "$iface" ]] || continue
        iface="${iface##*/}"
        local glob
        for glob in $_VIRTUAL_IFACE_GLOBS; do
            # shellcheck disable=SC2053  # glob match is the point
            if [[ "$iface" == $glob ]]; then
                found+=("$iface")
                break
            fi
        done
    done
    _join_by_comma "${found[@]+"${found[@]}"}"
}

_join_by_comma() {
    local out=""
    local item
    for item in "$@"; do
        out+="${out:+,}$item"
    done
    echo "$out"
}

# Open MPI's btl_tcp_if_exclude defaults to "127.0.0.1/8,sppp" -- it does NOT
# exclude a Docker bridge. With one present, every rank advertises 172.17.0.1
# as reachable, every peer dials it, and every peer reaches ITSELF:
#
#   received unexpected process identifier [[10111,1],1]
#   Open MPI accepted a TCP connection from what appears to be another Open MPI
#   process but cannot find a corresponding process entry for that peer
#
# The job does not fail -- it hangs. Observed on cluster iris (alinux2023arm,
# 2 x c8g.2xlarge, monitoring on): the 2-rank latency and bandwidth tests passed
# because both ranks landed on one node, then the 8-rank all-reduce wrote its
# header and deadlocked for 13h14m with TimeLimit=UNLIMITED, so Slurm never
# reaped it. allreduce.txt was 90 bytes of header and zero data rows.
#
# Exported rather than passed as --mca: $mpi_launcher may be Intel MPI's
# mpiexec, which rejects --mca outright, while OMPI_MCA_* is simply ignored by
# any launcher that is not Open MPI. Both channels are set -- btl is MPI
# traffic, oob is the out-of-band wire-up, and oob's default exclude list is
# EMPTY (verified with ompi_info on the head node), so it is the more exposed
# of the two.
#
# An operator who has already set either variable owns the decision; do not
# second-guess it. Note "" is a deliberate value (Open MPI reports it as
# data source: environment), so this tests for definedness, not emptiness.
_isolate_mpi_interfaces() {
    local excluded
    excluded=$(_virtual_ifaces)
    [[ -n "$excluded" ]] || return 0

    if [[ -z "${OMPI_MCA_btl_tcp_if_exclude+x}" ]]; then
        export OMPI_MCA_btl_tcp_if_exclude="lo,$excluded"
    fi
    if [[ -z "${OMPI_MCA_oob_tcp_if_exclude+x}" ]]; then
        export OMPI_MCA_oob_tcp_if_exclude="lo,$excluded"
    fi
    _info "Excluded virtual interfaces from MPI: $excluded"
}

_nproc_all() {
    # Total MPI ranks across all available slots (hostfile-aware if SLURM_NTASKS set)
    if [[ -n "${SLURM_NTASKS:-}" ]]; then
        echo "$SLURM_NTASKS"
    else
        nproc
    fi
}

_timestamp() { echo "$(date +%Y%m%d_%H%M%S)_$$"; }

# OSU, IOR, and HPCG are built by configure/make without -march=native, so they
# are portable within an architecture but not across one: an x86_64 binary will
# not load at all on Graviton ("Exec format error"). install runs on the head
# node; multi-node runs execute on compute nodes, which need not be the same
# instance family. Stamp the build arch so run can refuse up front instead of
# dying inside mpirun. STREAM does not rely on this guard -- it is compiled per
# microarchitecture on demand, see _stream_bin_path below.
_arch_stamp_path() { echo "${1}/.build_arch"; }

# _native_march: the microarchitecture gcc resolves -march=native to on this
# host (skylake-avx512, znver3, armv8.2-a+... on Graviton), or "unknown" when it
# cannot be read. `uname -m` cannot tell an Intel head node from an AMD GPU node
# -- both report x86_64 -- so it is not enough to decide whether a -march=native
# STREAM binary belongs on this node. The value becomes part of a filename, so
# anything outside [alnum]._- is folded to _ (aarch64 gcc answers with a
# +-separated feature list).
#
# gcc's exit status is deliberately ignored.  `gcc -march=native -Q --help=target`
# prints the option table and *then* exits non-zero, because -Q asks it to compile
# and no input file was given -- verified on the osiris head node (Ubuntu 24.04,
# gcc 13.3.0): the pipeline reports `PIPESTATUS=2 0`, gcc failing while awk
# succeeds on the line it already read.  The driver runs under `set -euo
# pipefail`, so piping straight out of gcc made the whole pipeline fail and the
# `|| resolved=""` fallback turned a perfectly good `skylake-avx512` into
# `unknown` on every node.  The symptom was invisible interactively, where the
# same pipeline prints the right answer and the status is discarded.  So capture
# the output first, ignoring the status, and let the parse decide: a march we
# cannot read yields an empty `resolved` and the caller's "unknown" path.
_native_march() {
    local raw="" resolved=""
    if command -v gcc >/dev/null 2>&1; then
        raw=$(gcc -march=native -Q --help=target 2>/dev/null) || true
        resolved=$(printf '%s\n' "$raw" \
            | awk '$1 == "-march=" { gsub(/[^[:alnum:]._-]/, "_", $2); print $2; exit }')
    fi
    echo "${resolved:-unknown}"
}

_write_arch_stamp() {
    uname -m > "$(_arch_stamp_path "$1")"
}

# _check_arch_stamp PREFIX: abort when the binaries under PREFIX were built for
# a different architecture than the host now running them. A missing stamp
# means the tree predates this check or was built by hand; warn, don't block.
_check_arch_stamp() {
    local stamp
    stamp="$(_arch_stamp_path "$1")"
    if [[ ! -f "$stamp" ]]; then
        echo "WARNING: no build architecture stamp at $stamp." >&2
        echo "         Cannot confirm these binaries match this host ($(uname -m))." >&2
        echo "         Re-run 'hpc-benchmark.sh install' if a benchmark fails to execute." >&2
        return 0
    fi
    local built_arch host_arch
    built_arch=$(< "$stamp")
    host_arch=$(uname -m)
    [[ "$built_arch" == "$host_arch" ]] || _die \
        "architecture mismatch: benchmarks were built for '$built_arch' but this host is '$host_arch'.
  This cluster mixes CPU architectures between the node that built the suite and
  the node running it, so the OSU, IOR, and HPCG binaries cannot execute here.
  Rebuild on this node class before running:
    ./hpc-benchmark.sh install
  If the head node and compute nodes differ in architecture, build and run the
  suite from within an allocation on the compute nodes, e.g.:
    srun --nodes=1 --pty bash
    ./hpc-benchmark.sh install"
}

# _cuda_home: the CUDA toolkit root on this host, or "" if there is none.
# CUDA_HOME wins if it is set and real; otherwise probe the versioned symlink
# ParallelCluster's DLAMI-based GPU images and NVIDIA's own .run installer both
# create. The header and the driver stub library are what OSU's configure
# actually tests for, so both must be present before claiming a usable toolkit.
_cuda_home() {
    local candidate
    for candidate in "${CUDA_HOME:-}" /usr/local/cuda /opt/nvidia/cuda; do
        [[ -n "$candidate" ]] || continue
        [[ -f "$candidate/include/cuda.h" ]] || continue
        compgen -G "$candidate/lib64/libcudart.*" >/dev/null 2>&1 || continue
        echo "$candidate"
        return 0
    done
    echo ""
}

# _host_has_gpu: does this host have an NVIDIA device visible right now?
# nvidia-smi is the only check that distinguishes a node with a driver and a
# device from one that merely has the toolkit installed. install runs on the
# head node, so on a CPU head node with a GPU compute queue this is correctly
# false: the cluster has GPUs, this machine does not, and a CUDA-linked binary
# built here could neither be compiled nor executed.
_host_has_gpu() {
    command -v nvidia-smi >/dev/null 2>&1 || return 1
    nvidia-smi -L 2>/dev/null | grep -q "^GPU [0-9]"
}

# _cuda_nvcc CUDA_HOME: the nvcc on this host, or "" if there is none. PATH
# first, then the toolkit's own bin/ -- CUDA's RPM/deb packages leave nvcc at
# /usr/local/cuda/bin without putting it on a login shell's PATH.
_cuda_nvcc() {
    local cuda_home="${1:-}"
    if command -v nvcc >/dev/null 2>&1; then
        command -v nvcc
        return 0
    fi
    if [[ -n "$cuda_home" && -x "$cuda_home/bin/nvcc" ]]; then
        echo "$cuda_home/bin/nvcc"
        return 0
    fi
    echo ""
}

# Where a CUDA-aware MPI might live. Hints for where to LOOK -- never what
# decides. _mpi_is_cuda_aware is the acceptance test, which is what keeps this
# correct on every base_os: no distro or path appears in any decision.
#
# The Debian/RHEL package conventions are here alongside ParallelCluster's own
# /opt/amazon because hpc-benchmark.sh is copy:d rather than rendered and has no
# cluster variables, so the layout of the node it lands on is not knowable in
# advance. A glob that misses costs a skipped optional test with a named reason,
# never a wrong number and never a hang.
_CUDA_MPI_GLOBS='/opt/amazon/openmpi* /opt/openmpi* /usr/lib64/openmpi* /usr/lib/*/openmpi /usr/lib/*/openmpi*'

# _mpi_root_of LAUNCHER: the install root of an MPI, given its launcher name or
# path -- i.e. the directory whose bin/ holds it. "" if it cannot be resolved.
# Used both to test the default MPI as a CUDA candidate and to record which MPI
# bin/osu was linked against.
_mpi_root_of() {
    local launcher="${1:-}" resolved
    [[ -n "$launcher" ]] || { echo ""; return 0; }
    resolved="$(command -v "$launcher" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || { echo ""; return 0; }
    (cd "$(dirname -- "$resolved")/.." 2>/dev/null && pwd) || echo ""
}

# _mpi_is_cuda_aware ROOT: does the Open MPI rooted at ROOT support CUDA?
#
# ompi_info's own answer, not a version comparison and not a path pattern.
# Measured at 12ms on a live node, so calling it per candidate is free. A
# non-Open-MPI tree (Intel, MPICH) has no ompi_info and is correctly rejected:
# -d cuda needs an MPI that can move device buffers, and this is the only
# portable way to ask.
_mpi_is_cuda_aware() {
    local root="${1:-}"
    [[ -n "$root" && -x "$root/bin/ompi_info" ]] || return 1
    [[ -x "$root/bin/mpirun" && -x "$root/bin/mpicc" ]] || return 1
    "$root/bin/ompi_info" --parsable --all 2>/dev/null \
        | grep -q '^mca:mpi:base:param:mpi_built_with_cuda_support:value:true$'
}

# _cuda_aware_mpi_root: the root of an MPI that can do device-to-device, or "".
#
# ParallelCluster's GPU AMIs ship TWO Open MPIs and the one on the default PATH
# is not the CUDA-aware one. On the AL2023 x86_64 image, measured directly:
# /opt/amazon/openmpi is 4.1.7 with mpi_built_with_cuda_support:false, while
# /opt/amazon/openmpi5 is 5.0.9amzn1 with it true, plus btl:smcuda and
# accelerator:cuda. Handing '-d cuda D D' to 4.1.7 does not fail -- it HANGS,
# forever, at the first message size, with both ranks spinning at 99.9% CPU and
# 0% GPU. Same failure class as the docker0 deadlock above.
#
# Search order, and step 2 is what makes this OS-agnostic rather than an AL2023
# special case: if a node's default MPI is already CUDA-aware, it is used and no
# path guessing happens at all. The globs are only consulted when the default
# cannot do the job, which is precisely the bug being fixed.
#
# HPC_BENCHMARK_CUDA_MPI is an operator override AND the suite's test seam --
# the same dual role HPC_BENCHMARK_NET_DIR plays for /sys/class/net. Without it
# no test could see this function's behavior: a developer's machine and CI have
# no MPI at all, so every assertion would pass against any implementation,
# including one with no probe in it.
_cuda_aware_mpi_root() {
    local override="${HPC_BENCHMARK_CUDA_MPI:-}"
    if [[ -n "$override" ]]; then
        if _mpi_is_cuda_aware "$override"; then
            echo "$override"
        else
            echo ""
        fi
        return 0
    fi

    local candidate
    candidate="$(_mpi_root_of mpirun)"
    if [[ -n "$candidate" ]] && _mpi_is_cuda_aware "$candidate"; then
        echo "$candidate"
        return 0
    fi

    local glob
    for glob in $_CUDA_MPI_GLOBS; do
        for candidate in $glob; do
            [[ -d "$candidate" ]] || continue
            if _mpi_is_cuda_aware "$candidate"; then
                echo "$candidate"
                return 0
            fi
        done
    done
    echo ""
}

# _mpi_lib_dirs ROOT: the lib directories of the MPI rooted at ROOT, colon-joined.
#
# Only for _cuda_mpi_ld_library_path below. lib64 is what the Amazon packages
# use; lib is checked because a source build or another distro's packaging may
# use it, and a directory that does not exist is skipped rather than emitted --
# a non-existent entry in LD_LIBRARY_PATH is harmless but makes the value a lie
# about what was found.
_mpi_lib_dirs() {
    local root="${1:-}" d out=""
    [[ -n "$root" ]] || { echo ""; return 0; }
    for d in "$root/lib64" "$root/lib"; do
        [[ -d "$d" ]] || continue
        out="${out:+$out:}$d"
    done
    echo "$out"
}

# _cuda_mpi_ld_library_path ROOT: LD_LIBRARY_PATH for launching the CUDA tree.
#
# Choosing the right launcher is NOT sufficient, and this is measured rather than
# reasoned. Both MPIs on a ParallelCluster GPU AMI ship SONAME libmpi.so.40
# (/opt/amazon/openmpi is .40.30.7, openmpi5 is .40.40.7), and the CUDA tree's
# binaries carry RUNPATH to openmpi5/lib64 -- but LD_LIBRARY_PATH outranks
# RUNPATH in the dynamic loader's search order, and the job script's own
# `module load openmpi` exports LD_LIBRARY_PATH=/opt/amazon/openmpi/lib64. So
# osu_latency loaded 4.1.7's libmpi under openmpi5's mpirun and died with
# `symbol lookup error: undefined symbol: ompi_mpi_instance_null` -- an Open MPI
# 5.x symbol that 4.1.7 does not define (`nm -D` on the live node: 1 in
# openmpi5's libmpi, 0 in openmpi's). Observed on osiris job 10, exit 127.
#
# Prepending rather than replacing: the value inherited from the environment may
# carry CUDA or other libraries the binary also needs, and dropping it to fix the
# MPI would trade one missing symbol for another. Prepending is enough because
# the loader takes the first match, verified on the node -- with openmpi5's
# lib64 first, ldd resolves libmpi.so.40 there while the host-to-host tree,
# which is launched separately and has its own RUNPATH, still resolves to
# openmpi's.
_cuda_mpi_ld_library_path() {
    local dirs
    dirs="$(_mpi_lib_dirs "${1:-}")"
    [[ -n "$dirs" ]] || { echo "${LD_LIBRARY_PATH:-}"; return 0; }
    echo "${dirs}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
}

# --with-cuda is what resolves nvcc, and both build paths must pass it. OSU
# substitutes NVCC from it (configure.ac: AC_ARG_WITH([cuda]) sets
# NVCC="$with_cuda/bin/nvcc", AC_SUBST([NVCC]), rendered as `NVCC = @NVCC@`), so
# a node carrying nvcc at $CUDA_HOME/bin but not on PATH -- where CUDA's RPM and
# deb packages leave it -- builds the kernels with an absolute path. That is
# version-dependent and is why OSU_VERSION has a floor: 7.4 hardcoded
# `NVCC = nvcc` in eleven Makefile.am files instead, which no configure argument
# could reach, and it died at make[4] with `nvcc: No such file or directory`.
# Dropping --with-cuda from either configure line brings that failure back.

# _osu_cuda_mode CUDA_HOME: "yes" or "basic", the --enable-cuda value this host
# can actually build.
#
# Both define _ENABLE_CUDA_ -> CUDA_ENABLED 1 (c/util/osu_util.h), which is the
# only thing '-d cuda' tests (c/util/osu_util.c), so device-to-device works
# either way. The difference is that =yes also sets build_cuda_kernels
# (configure.ac: AS_CASE([$enable_cuda], [yes], [build_cuda_kernels=yes; ...],
# [basic], [build_cuda=yes])), which adds util/kernel.cu to UTILITIES in eleven
# Makefile.am files and compiles it with NVCC = nvcc. configure never tests for
# nvcc, so on a node with the runtime but no full toolkit =yes configures
# cleanly and then fails in make.
#
# Nothing this suite runs needs the kernels: every _ENABLE_CUDA_KERNEL_ block in
# osu_latency.c and osu_bw.c is additionally guarded on managed memory
# (options.src == 'M'), and run only ever passes 'D D'. So =basic is the correct
# choice without nvcc rather than a degraded one.
_osu_cuda_mode() {
    if [[ -n "$(_cuda_nvcc "${1:-}")" ]]; then
        echo "yes"
    else
        echo "basic"
    fi
}

# _osu_cuda_enabled ROOT: was the OSU tree rooted at ROOT built with CUDA?
# run reads this to decide whether the device-to-device tests exist, rather
# than inferring it from the node it happens to be on. ROOT is the OSU tree
# itself, not the bin/ prefix -- there are two possible trees, see
# _osu_cuda_tree below.
_osu_cuda_stamp_path() { echo "${1}/.cuda_enabled"; }
_osu_cuda_enabled() { [[ -f "$(_osu_cuda_stamp_path "$1")" ]]; }

# _osu_cuda_stamp_mpi ROOT: the MPI root the tree at ROOT was BUILT against, or
# "" if the stamp predates this field.
#
# Field 3 of "<mode> <cuda_home> <mpi_root>". The binaries link against whichever
# MPI compiled them, so launching a 4.1.7-built tree with openmpi5's mpirun is an
# ABI mismatch -- and both ship SONAME libmpi.so.40, so it is not even a clean
# failure. run compares this against the MPI it is about to launch with.
#
# "" for a two-field stamp is deliberate and must degrade to a REBUILD, never to
# a skip: bin/ is shared storage that outlives clusters, so every tree an older
# driver built is two-field, and refusing those turns one upgrade into no device
# numbers on every existing cluster.
_osu_cuda_stamp_mpi() {
    local stamp
    stamp="$(_osu_cuda_stamp_path "$1")"
    [[ -f "$stamp" ]] || { echo ""; return 0; }
    awk 'NR==1 { print $3 }' "$stamp" 2>/dev/null || echo ""
}

# _osu_cuda_tree_matches_mpi ROOT MPI_ROOT: is the tree at ROOT usable with
# MPI_ROOT? Only an explicit, equal third field says yes.
_osu_cuda_tree_matches_mpi() {
    local recorded
    recorded="$(_osu_cuda_stamp_mpi "$1")"
    [[ -n "$recorded" && "$recorded" == "$2" ]]
}

# The OSU tree run builds for itself when the installed one has no CUDA. Kept
# separate from bin/osu so the host-to-host numbers stay comparable across runs
# and a GPU node cannot overwrite the head node's install. No -march suffix:
# OSU is not built with -march=native, so it is portable within an
# architecture, and cross-architecture is already refused by _check_arch_stamp.
_osu_cuda_tree() { echo "${1}/osu-cuda"; }
_osu_cuda_lock_path() { echo "${1}/.osu-cuda.lock"; }

# install caches the checksum-verified tarball here so run can build a CUDA tree
# on a compute node. Private-subnet nodes have no route to the download host, so
# re-fetching there is not an option.
_osu_src_path() { echo "${1}/src/osu-micro-benchmarks-${OSU_VERSION}.tar.gz"; }

# _verify_cached_src PATH SHA256: re-check a cached tarball before extracting it.
# bin/ is shared, writable storage that outlives any single cluster, so the file
# reaching run is not necessarily the file install verified. Bounds first: the
# error for a truncated or empty cache has to name the cause, and sha256sum on a
# missing file reports a shell-level failure that reads as a tampering alert.
_verify_cached_src() {
    local path="$1" expected_sha="$2" actual_sha
    [[ -n "$expected_sha" ]] || { echo "no expected checksum supplied for $path" >&2; return 1; }
    [[ -e "$path" ]] || { echo "cached source missing: $path" >&2; return 1; }
    [[ -f "$path" ]] || { echo "cached source is not a regular file: $path" >&2; return 1; }
    [[ -r "$path" ]] || { echo "cached source is not readable: $path" >&2; return 1; }
    [[ -s "$path" ]] || { echo "cached source is empty: $path" >&2; return 1; }
    actual_sha=$(sha256sum "$path" | awk '{print $1}') || {
        echo "could not checksum $path" >&2
        return 1
    }
    [[ -n "$actual_sha" ]] || { echo "empty checksum computed for $path" >&2; return 1; }
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        echo "checksum mismatch for cached source $path" >&2
        echo "  expected: $expected_sha" >&2
        echo "  actual:   $actual_sha" >&2
        echo "  bin/ is shared storage. Re-run 'hpc-benchmark.sh install' to refetch." >&2
        return 1
    fi
}

# STREAM binaries are named for the microarchitecture they were compiled for, so
# a head-node build and a GPU-node build coexist in the same shared bin/ instead
# of overwriting each other. run picks the one matching the node it is on and
# compiles it if absent, which is what makes the build follow the node class.
_stream_bin_path() { echo "${1}/stream-$(_native_march)"; }

# install caches the checksum-verified source here so the on-demand rebuild in
# run is a local compile. Compute nodes in a private subnet have no route to the
# internet, so re-downloading stream.c there is not an option.
_stream_src_path() { echo "${1}/src/stream.c"; }

# _compile_stream PREFIX [OUT]: build the cached source for this host's
# microarchitecture, into OUT or the shared per-march path.
# STREAM_ARRAY_SIZE: at least 4x L3 cache; 80M elements ~= 600 MB.
# NTIMES=20 gives a stable median; -O3 -march=native for real bandwidth.
_compile_stream() {
    local prefix="$1" bin="${2:-}" src
    src="$(_stream_src_path "$prefix")"
    [[ -n "$bin" ]] || bin="$(_stream_bin_path "$prefix")"
    [[ -f "$src" ]] || _die "STREAM source not cached at $src. Run 'hpc-benchmark.sh install --tools stream' first."
    _require_cmd gcc
    # Compile to a private path and rename into place. bin/ is on shared
    # storage, so two nodes of the same class can reach this concurrently; a
    # half-written file at the final path would already be executable.
    #
    # Failures are checked explicitly so the operator gets a reason; a bare
    # set -e abort here is silent. Callers must keep the `local v` / `v=$(...)`
    # split: a combined `local v=$(...)` is a builtin declaration, whose own
    # exit status is what set -e sees, so the caller would continue on and
    # execute a binary that was never produced.
    gcc -O3 -march=native -fopenmp \
        -DSTREAM_ARRAY_SIZE=80000000 -DNTIMES=20 \
        -o "$bin.$$" "$src" || { rm -f "$bin.$$"; _die "STREAM compile failed for $(_native_march)"; }
    mv -f "$bin.$$" "$bin" || _die "could not install the STREAM binary at $bin"
    echo "$bin"
}

# _build_osu_cuda PREFIX LOGDIR: build a CUDA-enabled OSU tree under PREFIX from
# the tarball install cached, for the node calling this. Returns non-zero on any
# failure without exiting -- the caller is run, which has already written its
# host-to-host results by this point.
#
# This is the OSU equivalent of _compile_stream's build-on-demand: install runs
# on the head node, and a CPU head node cannot produce a CUDA binary at all, so
# without this a GPU-queue job has no way to measure the device interconnect
# short of an interactive srun and a manual rebuild.
_build_osu_cuda() {
    local prefix="$1" log_dir="$2" mpi_root="${3:-}"
    local tree lock src cuda_home mode tmpdir rc=0
    tree="$(_osu_cuda_tree "$prefix")"
    lock="$(_osu_cuda_lock_path "$prefix")"
    src="$(_osu_src_path "$prefix")"

    cuda_home="$(_cuda_home)"
    [[ -n "$cuda_home" ]] || { echo "no CUDA toolkit on this node" >&2; return 1; }
    [[ -n "$mpi_root" ]] || { echo "no CUDA-aware MPI on this node" >&2; return 1; }
    command -v make >/dev/null 2>&1 || { echo "no make on this node" >&2; return 1; }
    _verify_cached_src "$src" "$OSU_SHA256" || return 1

    # The CUDA-aware MPI's OWN wrappers, by absolute path. A bare mpicc is the
    # default PATH one -- 4.1.7 on a ParallelCluster GPU AMI -- and a tree built
    # by it hangs under -d cuda no matter what launches it.
    #
    # Never LD_LIBRARY_PATH to reach them: both MPIs ship SONAME libmpi.so.40,
    # so exporting it silently redirects every 4.1.7-linked binary here too,
    # including the host-to-host benchmarks. It is also unnecessary -- both sets
    # of wrappers bake in RUNPATH to their own lib64 and resolve correctly under
    # env -u LD_LIBRARY_PATH (verified with ldd on a live node).
    local mpicc="$mpi_root/bin/mpicc" mpicxx="$mpi_root/bin/mpicxx"
    [[ -x "$mpicc" && -x "$mpicxx" ]] || {
        echo "no mpicc/mpicxx under $mpi_root" >&2
        return 1
    }

    # mkdir is the atomic primitive available everywhere. bin/ is shared storage,
    # so two jobs landing on GPU nodes at once would otherwise interleave their
    # make install into one prefix. A loser skips the device tests rather than
    # polling -- waiting inside an allocation burns node-hours for nothing.
    if ! mkdir "$lock" 2>/dev/null; then
        echo "another node is building the CUDA OSU tree (lock: $lock)." >&2
        echo "         Skipping the device-to-device tests for this run. If no other" >&2
        echo "         job is building and this persists, a previous job was killed" >&2
        echo "         before releasing it: rm -rf $lock" >&2
        return 1
    fi

    tmpdir=$(mktemp -d /tmp/hpc-benchmark-osu-cuda.XXXXXX) || { rmdir "$lock"; return 1; }

    mode="$(_osu_cuda_mode "$cuda_home")"
    _info "Building CUDA-enabled OSU for this node (--enable-cuda=$mode, MPI $mpi_root); this takes a few minutes."
    mkdir -p "$log_dir"
    # cd into the srcdir first. Invoking configure by absolute path from
    # elsewhere is a VPATH build: autoconf writes Makefile, config.status,
    # config.log, libtool and the c/ tree into the CWD, not the srcdir. The CWD
    # here is the job's submit directory, so that both littered shared storage
    # with build artifacts AND left `make -C srcdir` with no Makefile to read,
    # failing every device test with "No rule to make target 'install'".
    if (
        cd "$tmpdir" \
        && tar -xzf "$src" \
        && cd "osu-micro-benchmarks-${OSU_VERSION}" \
        && _try_build_step "CUDA OSU configure failed" "$log_dir/osu-cuda.log" \
            ./configure \
            --prefix="$tree" CC="$mpicc" CXX="$mpicxx" \
            "--enable-cuda=$mode" "--with-cuda=$cuda_home" \
        && _try_build_step "CUDA OSU build failed" "$log_dir/osu-cuda.log" \
            make -j"$(nproc)" install
    )
    then
        # Last, so the stamp is the completion marker: a half-built tree left by
        # a killed job is never selected by the caller.
        echo "$mode $cuda_home $mpi_root" > "$(_osu_cuda_stamp_path "$tree")" || rc=1
    else
        rc=1
    fi

    rm -rf "$tmpdir"
    rmdir "$lock" 2>/dev/null || rm -rf "$lock"
    return "$rc"
}

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
STREAM always runs single-node (it measures per-node memory bandwidth). It is
compiled -march=native, so run rebuilds it automatically on any node whose
microarchitecture has no binary in ./bin yet.

On a GPU node whose OSU has no CUDA support -- the case when install ran on a
CPU head node -- run builds a CUDA-enabled OSU under ./bin/osu-cuda and uses it
for the device-to-device tests only. That build takes a few minutes on the first
such job and is reused by every later one. If it cannot be done here, the
host-to-host results are still written and the reason is printed.
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
  report    Summarize results from a previous run

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

  # Summarize last run
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
            --prefix) [[ $# -ge 2 ]] || _die "$1 requires an argument"; prefix="$2"; shift 2 ;;
            --tools)  [[ $# -ge 2 ]] || _die "$1 requires an argument"; tools="$2";  shift 2 ;;
            -h|--help) _usage_install; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    _require_cmd gcc
    _require_cmd make
    _require_cmd sha256sum

    mkdir -p "$prefix"
    _build_tmpdir=$(mktemp -d /tmp/hpc-benchmark-build.XXXXXX)
    trap 'rm -rf "$_build_tmpdir"' EXIT

    # Build logs live under $prefix, not $_build_tmpdir, so they survive the
    # trap above and can be read after a failed install.
    local build_log_dir="$prefix/build_logs"
    mkdir -p "$build_log_dir"

    IFS=',' read -ra TOOL_LIST <<< "$tools"
    for tool in "${TOOL_LIST[@]}"; do
        case "$tool" in

        # ------------------------------------------------------------------ #
        # STREAM — single-node memory bandwidth                               #
        # ------------------------------------------------------------------ #
        stream)
            _info "Building STREAM for $(_native_march)..."
            _fetch "$_build_tmpdir/stream.c" "$STREAM_URL" "$STREAM_SHA256"
            mkdir -p "$(dirname "$(_stream_src_path "$prefix")")"
            cp "$_build_tmpdir/stream.c" "$(_stream_src_path "$prefix")"
            local stream_built
            stream_built=$(_compile_stream "$prefix") || exit 1
            _info "STREAM installed at $stream_built"
            ;;

        # ------------------------------------------------------------------ #
        # OSU Micro-Benchmarks — MPI point-to-point and collective            #
        # ------------------------------------------------------------------ #
        osu)
            _detect_mpi >/dev/null
            _info "Building OSU Micro-Benchmarks ${OSU_VERSION}..."
            _fetch "$_build_tmpdir/osu.tar.gz" "$OSU_URL" "$OSU_SHA256"
            # Cache the verified tarball so run can build a CUDA tree on a GPU
            # compute node without reaching the internet from a private subnet.
            mkdir -p "$(dirname "$(_osu_src_path "$prefix")")"
            cp "$_build_tmpdir/osu.tar.gz" "$(_osu_src_path "$prefix")"
            tar -xzf "$_build_tmpdir/osu.tar.gz" -C "$_build_tmpdir"
            local osu_src="$_build_tmpdir/osu-micro-benchmarks-${OSU_VERSION}"
            # CUDA follows the hardware on THIS node, not the cluster's
            # enable_gpu. install runs on the head node, and a CPU head node
            # fronting a GPU queue has no device and no toolkit -- yet any
            # --enable-cuda other than =no makes OSU's configure AC_MSG_ERROR
            # out on a missing -lcuda, -lcudart, or cuda.h (configure.ac).
            # Flipping it on from a cluster-level flag would therefore abort the
            # whole install, taking STREAM, IOR, and HPCG down with OSU.
            # Whether the toolkit can do =yes or only =basic is a second
            # question, answered by _osu_cuda_mode.
            local osu_cuda_home="" osu_cuda_args=() osu_cuda_mode=""
            if _host_has_gpu; then
                osu_cuda_home="$(_cuda_home)"
                if [[ -n "$osu_cuda_home" ]]; then
                    osu_cuda_mode="$(_osu_cuda_mode "$osu_cuda_home")"
                    osu_cuda_args=("--enable-cuda=$osu_cuda_mode" "--with-cuda=$osu_cuda_home")
                    _info "NVIDIA device and CUDA toolkit found at $osu_cuda_home; building OSU with --enable-cuda=$osu_cuda_mode."
                    if [[ "$osu_cuda_mode" == "basic" ]]; then
                        _info "No nvcc on this node, so the managed-memory CUDA kernels are not built. The device-to-device tests this suite runs do not use them."
                    fi
                else
                    osu_cuda_args=(--enable-cuda=no)
                    echo "WARNING: this node has an NVIDIA device but no CUDA toolkit" >&2
                    echo "         (no include/cuda.h + lib64/libcudart.* under CUDA_HOME," >&2
                    echo "         /usr/local/cuda, or /opt/nvidia/cuda)." >&2
                    echo "         Building OSU without CUDA: device-to-device tests will" >&2
                    echo "         not be compiled in. Install the CUDA toolkit and re-run" >&2
                    echo "         'hpc-benchmark.sh install --tools osu' to get them." >&2
                fi
            else
                osu_cuda_args=(--enable-cuda=no)
                _info "No NVIDIA device on this node; building OSU for host-to-host MPI only."
            fi
            pushd "$osu_src" >/dev/null
            _build_step "OSU configure failed" "$build_log_dir/osu.log" \
                ./configure --prefix="$prefix/osu" CC=mpicc CXX=mpicxx "${osu_cuda_args[@]}"
            _build_step "OSU build failed" "$build_log_dir/osu.log" \
                make -j"$(nproc)" install
            popd >/dev/null
            # Record what was actually built so run does not have to guess from
            # the node it lands on -- bin/ is shared storage and the node running
            # the job is frequently not the node that compiled this.
            #
            # Field 3 is the MPI this tree is linked against, which run compares
            # against the CUDA-aware MPI it would launch with. bin/osu serves the
            # host-to-host tests too, so it is deliberately built with the DEFAULT
            # mpicc rather than a CUDA-aware one -- changing that would silently
            # change what every headline latency number means. On an AMI whose
            # default MPI is not CUDA-aware, the stamp therefore records a
            # non-matching root and run builds bin/osu-cuda instead, which is
            # correct: this tree cannot do -d cuda no matter what launches it.
            if [[ -n "$osu_cuda_home" ]]; then
                local osu_mpi_root=""
                osu_mpi_root="$(_mpi_root_of "$(_detect_mpi)")"
                echo "$osu_cuda_mode $osu_cuda_home $osu_mpi_root" > "$(_osu_cuda_stamp_path "$prefix/osu")"
                if _mpi_is_cuda_aware "$osu_mpi_root"; then
                    _info "OSU is linked against $osu_mpi_root, which is CUDA-aware; the device-to-device tests will use this tree."
                else
                    _info "OSU is linked against $osu_mpi_root, which is NOT CUDA-aware (ompi_info reports mpi_built_with_cuda_support:false), so 'run' will build a second tree for the device-to-device tests against whichever CUDA-aware MPI the compute node has. Set HPC_BENCHMARK_CUDA_MPI to name one explicitly."
                fi
            else
                rm -f "$(_osu_cuda_stamp_path "$prefix/osu")"
            fi
            _info "OSU installed at $prefix/osu/"
            ;;

        # ------------------------------------------------------------------ #
        # IOR — parallel I/O                                                  #
        # ------------------------------------------------------------------ #
        ior)
            _detect_mpi >/dev/null
            _info "Building IOR ${IOR_VERSION}..."
            _fetch "$_build_tmpdir/ior.tar.gz" "$IOR_URL" "$IOR_SHA256"
            tar -xzf "$_build_tmpdir/ior.tar.gz" -C "$_build_tmpdir"
            local ior_src="$_build_tmpdir/ior-${IOR_VERSION}"
            pushd "$ior_src" >/dev/null
            _build_step "IOR configure failed" "$build_log_dir/ior.log" \
                ./configure --prefix="$prefix/ior" CC=mpicc
            _build_step "IOR build failed" "$build_log_dir/ior.log" \
                make -j"$(nproc)" install
            popd >/dev/null
            _info "IOR installed at $prefix/ior/"
            ;;

        # ------------------------------------------------------------------ #
        # HPCG — sparse CG solver scaling                                     #
        # ------------------------------------------------------------------ #
        hpcg)
            _detect_mpi >/dev/null
            _info "Building HPCG ${HPCG_VERSION}..."
            _fetch "$_build_tmpdir/hpcg.tar.gz" "$HPCG_URL" "$HPCG_SHA256"
            tar -xzf "$_build_tmpdir/hpcg.tar.gz" -C "$_build_tmpdir"
            local hpcg_src
            hpcg_src=$(find "$_build_tmpdir" -maxdepth 1 -type d -name 'hpcg-*' | sort | head -1)
            [[ -d "$hpcg_src" ]] || _die "HPCG source directory not found after extract"
            # HPCG's configure takes the suffix of a setup/Make.<TARGET> file
            # that must already exist in the tarball; it does not generate one.
            # An unknown target prints "Please create the configuration file"
            # and exits 127 without writing a Makefile, so the failure surfaces
            # at the following make. MPI_GCC does not exist in 3.1 -- the
            # MPI+OpenMP target it ships is MPI_GCC_OMP. Check before running so
            # a future retarget fails on the missing file, not on make.
            local hpcg_setup="$hpcg_src/setup/Make.MPI_GCC_OMP"
            [[ -f "$hpcg_setup" ]] || _die \
                "HPCG setup file not found: $hpcg_setup
  configure only accepts a target whose setup/Make.<TARGET> file ships in the
  tarball. Available targets in this source tree:
$(find "$hpcg_src/setup" -name 'Make.*' -exec basename {} \; 2>/dev/null | sed 's/^Make\./    /' | sort)"

            # HPCG 3.1 predates OpenMP 4.0, which stopped treating a const
            # scalar as predetermined-shared. ComputeResidual.cpp's
            # `parallel default(none)` therefore omits `n` from its shared
            # clause, and every GCC from 9 on rejects the enclosed `omp for`
            # with "'n' not specified in enclosing 'parallel'". This is the only
            # default(none) region in the tree. Add `n` rather than dropping
            # default(none): both compile, but keeping it preserves the
            # compiler's check that no other variable is captured implicitly.
            local hpcg_residual="$hpcg_src/src/ComputeResidual.cpp"
            [[ -f "$hpcg_residual" ]] || _die "HPCG source not found: $hpcg_residual"
            # Anchored on a shared list that does not already start with `n,` so
            # a second install into the same tree cannot yield `shared(n, n, ...)`.
            sed -i.bak \
                's/\(#pragma omp parallel default(none) shared(\)\(n, \)\{0,1\}/\1n, /' \
                "$hpcg_residual"

            mkdir -p "$hpcg_src/build"
            pushd "$hpcg_src/build" >/dev/null
            _build_step "HPCG configure failed (setup target MPI_GCC_OMP)" \
                "$build_log_dir/hpcg.log" ../configure MPI_GCC_OMP
            _build_step "HPCG build failed" "$build_log_dir/hpcg.log" \
                make -j"$(nproc)"
            mkdir -p "$prefix/hpcg/bin"
            cp bin/xhpcg "$prefix/hpcg/bin/"
            cp "$hpcg_src/bin/hpcg.dat" "$prefix/hpcg/"
            popd >/dev/null
            _info "HPCG installed at $prefix/hpcg/"
            ;;

        *) _die "unknown tool '$tool'. Choose from: stream,osu,ior,hpcg" ;;
        esac
    done

    _write_arch_stamp "$prefix"
    _info "Install complete ($(uname -m)). Run 'hpc-benchmark.sh run' to execute benchmarks."
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

    # Reject leading zeros (e.g. "010") — bash's arithmetic contexts below
    # interpret leading-zero numerals as octal, silently miscomputing rank
    # counts or crashing outright on digits 8/9. Cap at 7 digits (max
    # 9999999) so no value anywhere near bash's signed 64-bit arithmetic
    # range (~9.2e18) can reach the "nodes -gt 1" Slurm-allocation check or
    # the "nodes * ppn" multiplication below — both wrap silently on
    # overflow instead of erroring, which would bypass the Slurm gate.
    [[ -z "$nodes" || "$nodes" =~ ^(0|[1-9][0-9]{0,6})$ ]] || _die "--nodes must be a positive integer (max 9999999) with no leading zeros"
    [[ "$ppn" =~ ^(0|[1-9][0-9]{0,6})$ ]] || _die "--ppn must be a positive integer (max 9999999) with no leading zeros"

    # The stamp guards the configure/make-built tools only. STREAM compiles
    # itself for whatever node it lands on, so a stream-only run on a foreign
    # architecture is valid and must not be blocked by binaries it never uses.
    if [[ "$tests" != "stream" ]]; then
        _check_arch_stamp "$BENCH_BIN"
    fi

    local mpi_launcher
    mpi_launcher=$(_detect_mpi)
    _isolate_mpi_interfaces

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

    # OSU pt2pt is hardcoded -n 2 (latency, bandwidth, and the two CUDA
    # device-to-device variants), so a 1-slot allocation cannot run it. Open MPI
    # reads Slurm's slot count and refuses with "There are not enough slots
    # available in the system to satisfy the 2 slots that were requested",
    # naming the binary path and not the cause -- which sent an operator after
    # the wrong thing on the g6 osiris build of 2026-07-29. The multi-node gate
    # above cannot catch this: it tests "nodes -gt 1" only, so --nodes 1 --ppn 1
    # sails past it. Diagnose it here, by name, before any results directory is
    # created.
    if [[ ",$tests," == *,osu,* && "$total_ranks" -lt 2 ]]; then
        _die "OSU needs at least 2 MPI slots; this allocation has $total_ranks.
  The latency and bandwidth tests run 2 ranks regardless of --ppn.
  Inside Slurm, request the slots up front:
    srun --nodes=${nodes:-1} --ntasks=2 --pty bash
    ./hpc-benchmark.sh run --tests $tests --nodes ${nodes:-1} --ppn 2"
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
            local stream_bin
            stream_bin="$(_stream_bin_path "$BENCH_BIN")"
            if [[ "$(_native_march)" == "unknown" ]]; then
                # gcc could not name the target, so the shared per-march path
                # cannot distinguish node classes and would hand every node the
                # first node's binary. Build into the run's own results dir
                # instead: correct for this node, cached for nobody.
                if command -v gcc >/dev/null 2>&1 && [[ -f "$(_stream_src_path "$BENCH_BIN")" ]]; then
                    echo "WARNING: gcc cannot report the -march=native target on this node," >&2
                    echo "         so STREAM cannot be cached per microarchitecture." >&2
                    echo "         Building a throwaway binary for this run instead." >&2
                    stream_bin=$(_compile_stream "$BENCH_BIN" "$results_dir/$ts/stream.bin") \
                        || _die "STREAM could not be built on this node."
                else
                    stream_bin=$(find "$BENCH_BIN" -maxdepth 1 -type f -name 'stream-*' -perm -u+x | sort | head -1)
                    [[ -n "$stream_bin" ]] || _die \
                        "no STREAM binary in $BENCH_BIN and this node cannot build one. Run 'hpc-benchmark.sh install --tools stream' on a node with gcc."
                    echo "WARNING: cannot build STREAM on this node, so a binary compiled" >&2
                    echo "         elsewhere is being used: $(basename "$stream_bin")." >&2
                    echo "         Reported bandwidth may under-report this node." >&2
                fi
            elif [[ ! -x "$stream_bin" ]]; then
                [[ -f "$(_stream_src_path "$BENCH_BIN")" ]] || _die \
                    "STREAM not installed. Run 'hpc-benchmark.sh install --tools stream' first."
                # The head node's binary is tuned for the head node's core.
                # Rather than run it here and under-report, compile the cached
                # source for this node -- a few seconds, and the result is
                # reused by every later job on the same node class.
                _info "No STREAM binary for $(_native_march); compiling for this node..."
                _compile_stream "$BENCH_BIN" >/dev/null
            fi
            _info "Running $(basename "$stream_bin") (single node, $(nproc) threads)..."
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

            # Device-to-device needs THREE halves, not two: a CUDA-linked binary,
            # an actual device on the node running it, and an MPI that can move
            # device buffers. Each fails differently -- '-d cuda' against a
            # non-CUDA build is rejected as an unknown option, a CUDA build on a
            # CPU node dies in cudaSetDevice, and a non-CUDA-aware MPI HANGS
            # forever, which is the worst of the three because nothing reports it.
            #
            # The device half is decided here and cannot be built; the binary half
            # can, on this node, from the tarball install cached. Only the
            # device-to-device tests move to that tree, and only they use the
            # CUDA-aware MPI -- the host-to-host numbers above stay on bin/osu and
            # on the default launcher so they remain comparable across runs and
            # across node classes.
            local osu_cuda_pt2pt="" cuda_mpi_root="" cuda_mpi_launcher=""
            if _host_has_gpu; then
                cuda_mpi_root="$(_cuda_aware_mpi_root)"
                cuda_mpi_launcher="$cuda_mpi_root/bin/mpirun"
            fi
            local installed_tree="$BENCH_BIN/osu"
            local runtime_tree
            runtime_tree="$(_osu_cuda_tree "$BENCH_BIN")"
            if ! _host_has_gpu; then
                if _osu_cuda_enabled "$installed_tree" || _osu_cuda_enabled "$runtime_tree"; then
                    echo "NOTE: a CUDA-enabled OSU is available but this node has no NVIDIA" >&2
                    echo "      device, so the device-to-device tests were skipped." >&2
                fi
            elif [[ -z "$cuda_mpi_root" ]]; then
                # Never launch the d2d pair with an MPI that cannot do it. Under
                # 4.1.7 the identical command prints its header and then spins at
                # 99.9% CPU and 0% GPU until the allocation's time limit -- and
                # with TimeLimit=UNLIMITED, that is forever.
                echo "NOTE: no CUDA-aware MPI was found on this node, so the device-to-device" >&2
                echo "      tests were skipped rather than launched into a hang. Check" >&2
                echo "      'ompi_info --parsable --all | grep mpi_built_with_cuda_support'" >&2
                echo "      for each MPI installed, or set HPC_BENCHMARK_CUDA_MPI to the root" >&2
                echo "      of one (ParallelCluster GPU AMIs ship it as /opt/amazon/openmpi5)." >&2
                echo "      The host-to-host results above are unaffected." >&2
            elif _osu_cuda_tree_matches_mpi "$installed_tree" "$cuda_mpi_root"; then
                osu_cuda_pt2pt="$osu_pt2pt"
            elif _osu_cuda_tree_matches_mpi "$runtime_tree" "$cuda_mpi_root"; then
                osu_cuda_pt2pt="$runtime_tree/libexec/osu-micro-benchmarks/mpi/pt2pt"
            elif _build_osu_cuda "$BENCH_BIN" "$BENCH_BIN/build_logs" "$cuda_mpi_root"; then
                osu_cuda_pt2pt="$runtime_tree/libexec/osu-micro-benchmarks/mpi/pt2pt"
            else
                echo "NOTE: this node has an NVIDIA device but no CUDA-enabled OSU built" >&2
                echo "      against $cuda_mpi_root, and one could not be built here (reason" >&2
                echo "      above). The host-to-host results are unaffected; only the" >&2
                echo "      device-to-device tests were skipped." >&2
            fi

            if [[ -n "$osu_cuda_pt2pt" ]]; then
                # $cuda_mpi_launcher, not $mpi_launcher: these binaries are linked
                # against $cuda_mpi_root's libmpi, and both MPIs on a PCluster GPU
                # AMI ship SONAME libmpi.so.40, so launching with the wrong one is
                # not even a clean failure.
                #
                # And the launcher alone is not enough. LD_LIBRARY_PATH outranks
                # the RUNPATH these binaries carry, the job script's own `module
                # load openmpi` exports the OTHER MPI's lib64, and -x forwards the
                # value to the ranks -- without it prterun ships its own
                # environment and the fix never reaches the process that dies. See
                # _cuda_mpi_ld_library_path.
                local cuda_llp
                cuda_llp="$(_cuda_mpi_ld_library_path "$cuda_mpi_root")"

                _info "Running OSU latency, device-to-device (2 ranks, -d cuda, MPI $cuda_mpi_root)..."
                LD_LIBRARY_PATH="$cuda_llp" \
                    $cuda_mpi_launcher -n 2 -x LD_LIBRARY_PATH \
                    "$osu_cuda_pt2pt/osu_latency" -d cuda D D \
                    | tee "$results_dir/$ts/osu/latency_cuda.txt"

                _info "Running OSU bandwidth, device-to-device (2 ranks, -d cuda, MPI $cuda_mpi_root)..."
                LD_LIBRARY_PATH="$cuda_llp" \
                    $cuda_mpi_launcher -n 2 -x LD_LIBRARY_PATH \
                    "$osu_cuda_pt2pt/osu_bw" -d cuda D D \
                    | tee "$results_dir/$ts/osu/bandwidth_cuda.txt"
            fi

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
            # $ts in the filename, not a bare ior_testfile: --fs-path names a
            # FILESYSTEM to stress, so two concurrent jobs legitimately share it
            # -- and with one fixed name they shared the test FILES too. Both saw
            # "exists already and will be overwritten", then the first to finish
            # ran the rm below and pulled the other's files out from under it,
            # which IOR reports as `ERROR: stat(...) failed, (aiori-POSIX.c:866)`
            # with no hint that another job caused it. Observed on osiris with
            # jobs 7 and 8 overlapping by four seconds. $ts is date+PID, the same
            # value that makes $results_dir/$ts unique, so the rm still only ever
            # matches this run's own files.
            #
            # Write then read; -F = file-per-process; -C = reorder tasks to avoid cache
            # -e = fsync after write, so write bandwidth reflects the filesystem
            # rather than the page cache
            $mpi_launcher -n "$total_ranks" "$ior_bin" \
                -a POSIX -F -w -r -C -e \
                -t 1m -b "$ior_size" \
                -o "$fs_path/ior_testfile.$ts" \
                -v \
                | tee "$results_dir/$ts/ior.txt"

            rm -f "$fs_path/ior_testfile.$ts"*
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
            --results-dir) [[ $# -ge 2 ]] || _die "$1 requires an argument"; results_dir="$2"; shift 2 ;;
            -h|--help) _usage_report; exit 0 ;;
            *) _die "unknown option: $1" ;;
        esac
    done

    # If passed the parent dir, use the most recent timestamped run. Check
    # for evidence of any single test's output (stream.txt, osu/, ior.txt,
    # hpcg/) — a leaf dir may contain only a subset of these depending on
    # which --tests were run. cmd.txt also counts as leaf evidence: a run
    # that _die'd before any benchmark produced output still has cmd.txt
    # recording the invocation, and that's worth showing rather than
    # descending into a nonexistent timestamped subdirectory.
    if [[ ! -f "$results_dir/stream.txt" && ! -d "$results_dir/osu" \
          && ! -f "$results_dir/ior.txt" && ! -d "$results_dir/hpcg" \
          && ! -f "$results_dir/cmd.txt" ]]; then
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
        awk 'NF==2 && $2+0>0 && $2+0>max+0 {max=$2; msg=$1} END {printf "  %s bytes -> %s MB/s\n", msg, max}' \
            "$results_dir/osu/bandwidth.txt" 2>/dev/null || true
    fi

    # ---- OSU device-to-device (only present when OSU was built with CUDA on a
    # ---- node with a GPU, so absence here is normal on a CPU cluster)
    if [[ -f "$results_dir/osu/latency_cuda.txt" ]]; then
        echo ""
        echo "--- OSU MPI Latency, device-to-device (pt2pt, 8-byte message) ---"
        awk 'NF==2 && $1=="8" {printf "  %s bytes -> %s us\n", $1, $2; exit}' \
            "$results_dir/osu/latency_cuda.txt" || true
    fi
    if [[ -f "$results_dir/osu/bandwidth_cuda.txt" ]]; then
        echo "--- OSU MPI Bandwidth, device-to-device (pt2pt, peak) ---"
        awk 'NF==2 && $2+0>0 && $2+0>max+0 {max=$2; msg=$1} END {printf "  %s bytes -> %s MB/s\n", msg, max}' \
            "$results_dir/osu/bandwidth_cuda.txt" 2>/dev/null || true
    fi

    # ---- IOR ----
    if [[ -f "$results_dir/ior.txt" ]]; then
        echo ""
        echo "--- IOR (parallel I/O) ---"
        grep -E "^(write|read)" "$results_dir/ior.txt" \
            | awk '{printf "  %-6s %s MiB/s\n", $1, $2}' || true
    fi

    # ---- HPCG ----
    local hpcg_result=""
    if [[ -d "$results_dir/hpcg" ]]; then
        hpcg_result=$(find "$results_dir/hpcg" -maxdepth 1 -name 'HPCG-Benchmark_*.txt' 2>/dev/null | sort | head -1) || true
    fi
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

# Sourced with HPC_BENCHMARK_LIB_ONLY set: define the helpers and stop, so the
# test suite can drive _fetch and friends without running a build.
[[ -n "${HPC_BENCHMARK_LIB_ONLY:-}" ]] && return 0

# Below the guard, not at the top of the file: `set` applies to the shell that
# runs it, so at the top it leaked into any shell that sourced this file.  The
# documented library entry point above therefore handed an interactive caller a
# shell where the next non-zero command exited the session -- which closed two
# SSH sessions while debugging _native_march.  Everything the sourced region
# executes is an assignment plus the SCRIPT_DIR substitution on line 32, none of
# which can fail, so nothing above needs -e.  Every function that does is called
# from the dispatch block below, so cmd_install/cmd_run/cmd_report and the
# concurrency-sensitive code they reach (_compile_stream's mv, _build_osu_cuda's
# mkdir lock) run under the same options as before.  Do not move this back up.
set -euo pipefail

[[ $# -eq 0 ]] && { _usage_main; exit 1; }

command="$1"; shift
case "$command" in
    install) cmd_install "$@" ;;
    run)     cmd_run     "$@" ;;
    report)  cmd_report  "$@" ;;
    -h|--help) _usage_main; exit 0 ;;
    *) _die "unknown command '$command'. Run 'hpc-benchmark.sh --help'." ;;
esac
