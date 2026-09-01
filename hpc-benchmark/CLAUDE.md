# Claude Instructions — hpc-benchmark/

Loaded when working in `hpc-benchmark/`. Full rationale, incident history, and
test-name citations live in `hpc-benchmark/CLAUDE.local.md` (gitignored, local
development only). The root `CLAUDE.md` holds everything else.

`hpc-benchmark.sh` is `copy:`d to the node, not rendered, so it has no cluster
variables — every decision it makes is a runtime probe. Gates: `make
shellcheck` plus the shell-surface tests in `tests/test_shell_surfaces.py`,
which source the driver as a library.

## Constraints

- **`hpc-benchmark.sh` returns early when `HPC_BENCHMARK_LIB_ONLY` is set**
  so the test suite can source it as a library. `set -euo pipefail` sits
  **below** that guard, deliberately — leaking `-e` into a sourcing shell
  closed two SSH sessions while debugging `_native_march`, and the driver
  says `Do not move this back up`. `SCRIPT_DIR="$(cd "$(dirname -- "$0")"
  && pwd)"` does still run on `source`, above the guard.
  `dirname --` is required (an interactive shell's `$0` is `-bash`, which
  `dirname` without `--` parses as flags); do not add a `|| SCRIPT_DIR="$PWD"`
  fallback (the `cd` cannot fail, so it's unreachable dead code).
- **`gcc -march=native -Q --help=target` exits non-zero even on success** —
  it prints the option table then fails with "no input files" on stderr.
  `_native_march` must capture gcc's stdout on its own command with `||
  true` and parse the captured text separately, never pipe gcc directly
  into the parser under `set -euo pipefail` (the pipeline's failure status
  propagates and silently returns `unknown`). Anchor the parse on `$1`
  being exactly `-march=`, not a substring match (a neighboring `-mtune=`
  row and a trailing "Known valid arguments" line share the same shape).
- **STREAM is built once per microarchitecture, not once per cluster** —
  `uname -m` can't distinguish an Intel head node from an AMD GPU node, both
  report `x86_64`. `run` compiles `bin/stream-<march>` keyed on what gcc's
  probe resolves to; `bin/` is shared storage, so writes go to a temp name
  and `mv -f` into place.
- **OSU's `--enable-cuda` follows the hardware on the node running
  `install`, never a cluster-level flag.** `enable_gpu` describes the queue;
  `install` compiles on the head node, which may have no GPU at all.
  Forcing the flag on aborts the whole install (STREAM/IOR/HPCG included) if
  the builder lacks CUDA. Requires both a real GPU (`_host_has_gpu`) and a
  CUDA toolkit (`_cuda_home`) before enabling; the choice is recorded in
  `bin/osu/.cuda_enabled` and cleared on a non-CUDA rebuild.
- **`--enable-cuda` is derived, never fixed.** `_osu_cuda_mode` returns
  `yes` when `_cuda_nvcc` finds a compiler and `basic` otherwise; do not
  pin either. Do not "upgrade" the **fallback** to `=yes`: `=yes` compiles
  CUDA kernels and requires `nvcc`, which `configure` never checks for — a
  node with CUDA runtime libraries but no compiler configures cleanly and
  then fails at `make`. `=basic` is sufficient for everything this suite
  measures (`-d cuda D D` only).
- **`OSU_VERSION` has a floor of 7.5.2** — 7.4 cannot compile against CUDA
  13 (removed 4-arg `cudaMemPrefetchAsync`) and hardcodes `NVCC = nvcc`
  with no `--with-cuda` substitution, so the kernel build silently resolves
  the wrong (or missing) `nvcc`. Do not drop below 7.5.2 or hand-patch around
  either issue — both are fixed upstream in 7.5.2.
- **When `install` couldn't build CUDA, `run` builds a second OSU tree
  (`bin/osu-cuda`) on the GPU node at run time.** That build must never fail
  the run (use the non-fatal `_try_build_step`, not the exiting
  `_build_step`); the cached source tarball is re-verified against its
  checksum before reuse; concurrent jobs coordinate via a `mkdir`-based lock
  (`bin/.osu-cuda.lock`) where the loser skips device tests rather than
  waiting.
- **`_build_osu_cuda` must `cd` into the source directory before running
  `configure`** — invoking `configure` by absolute path from elsewhere is a
  VPATH build; Autoconf writes `Makefile` etc. into the CWD, and `make -C
  <srcdir> install` then finds no `Makefile` there. The failure is silent:
  the run exits 0 (this build is non-fatal by design) with only a `NOTE:`
  on stderr.
- **A CUDA-linked binary and a GPU are not enough for `-d cuda` — the MPI
  launching it must be CUDA-aware too.** ParallelCluster GPU AMIs ship two
  Open MPIs; the one on the default `PATH` is typically *not*
  CUDA-aware, and handing it `-d cuda` doesn't fail, it **hangs** (both
  ranks spin at ~100% CPU, 0% GPU, no timeout). Detect via `ompi_info
  --parsable --all` reporting `mpi_built_with_cuda_support:value:true` —
  never by version number or path pattern. Both the tree-builder (`mpicc`)
  and the launcher (`mpirun`) must come from the same CUDA-aware root.
  `HPC_BENCHMARK_CUDA_MPI` is an operator override *and* the test seam (no
  dev machine or CI host has any MPI to probe).
- **`LD_LIBRARY_PATH` must be set only as a per-command prefix on the two
  `-d cuda` launches, never `export`ed.** Both MPIs ship the same SONAME
  (`libmpi.so.40`), and `LD_LIBRARY_PATH` outranks the binary's own RUNPATH
  — an `export` (or a bare top-level assignment) silently redirects the
  host-to-host tests onto the wrong MPI too, changing every headline number
  with no indication. Prepend to the inherited value, don't replace it, and
  pass `-x LD_LIBRARY_PATH` so the launcher forwards it to the ranks.
- **The IOR test files are named with `$ts` (a per-run timestamp), never a
  fixed name.** Two concurrent jobs sharing one `--fs-path` is expected and
  meaningful, but a fixed object name means one job's cleanup (`rm -f
  ".../ior_testfile"*`) deletes the other's in-flight files — which IOR
  reports as a bare filesystem error with nothing implicating the other job.
- **Every network interface that carries the same address on every node
  must be excluded from MPI before the first launch** (Docker's bridge,
  `docker0`, is the recurring case — `enable_monitoring` installs Docker on
  every node). Open MPI doesn't fail on the collision, it **hangs**
  indefinitely. Set both `OMPI_MCA_btl_tcp_if_exclude` and
  `OMPI_MCA_oob_tcp_if_exclude` via environment variables (never `--mca` —
  some launchers reject that flag outright), and detect virtual interfaces
  by scanning `/sys/class/net` at runtime (the driver is `copy:`d with no
  cluster variables to derive this from).
- **OSU pt2pt tests are hardcoded to 2 ranks** (`-n 2`, regardless of
  `--ppn`). `cmd_run` must refuse a 1-slot allocation before creating the
  results directory — Open MPI's own rejection message names the binary
  path, not the real cause, and sends an operator chasing the wrong thing.
  Match test names on `[[ ",$tests," == *,osu,* ]]` (not a bare `*osu*`
  substring, which would also fire on an unrelated future test name
  containing "osu").
- **`set -e` does not propagate through a command substitution that's part
  of a builtin declaration** (`local v=$(f)`, `export v=$(f)`, `declare`,
  `readonly`, `typeset` all swallow the failure) — only a **plain**
  assignment (`v=$(f)` as its own statement) propagates it. Keep the
  explicit two-line `local v` / `v=$(...)` split at every call site that
  needs failures to reach the caller; collapsing it silently converts an
  aborting call into a continuing one.
