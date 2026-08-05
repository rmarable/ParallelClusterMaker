"""
Regression tests guarding against secret-handling and gating regressions in the
Ansible playbooks — that the SSH private key (.pem) is never swept into the
generic cluster_data_dir -> S3 sync, that teardown flags actually gate teardown
behavior, and that individual task parameters the build depends on (download
checksums, SSM parameter paths, feature gates) cannot silently change.
"""

import json
import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tasks(playbook_path):
    with open(playbook_path) as fh:
        plays = yaml.safe_load(fh)
    tasks = []
    for play in plays:
        tasks.extend(play.get("tasks", []))
    return tasks


def _find_task(tasks, name):
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"task not found: {name!r}")


def _leaf_tasks(playbook_path):
    """Every executable task, descending into blocks and carrying the `when:`
    conditions a block imposes on the tasks inside it.

    Returns a list of (name, task_dict, effective_conditions). `_load_tasks`
    only sees the top level, so a task nested in a `block:` — which is where the
    monitoring and benchmark staging tasks live — is invisible to it.
    """
    out = []

    def walk(tasks, inherited):
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            own = task.get("when")
            conds = inherited + (own if isinstance(own, list) else [own] if own else [])
            if "block" in task:
                walk(task["block"], conds)
            else:
                out.append((task.get("name"), task, [str(c) for c in conds]))

    with open(playbook_path) as fh:
        for play in yaml.safe_load(fh):
            walk(play.get("tasks"), [])
    return out


def _find_leaf(playbook, name):
    for got, task, conds in _leaf_tasks(os.path.join(REPO_ROOT, "src", playbook)):
        if got == name:
            return task, conds
    raise AssertionError(f"task not found in {playbook}: {name!r}")


class TestCreatePlaybookExcludesPrivateKeyFromS3Sync:
    def test_s3_sync_excludes_pem_files(self):
        tasks = _load_tasks(os.path.join(REPO_ROOT, "src", "create_pcluster.yml"))
        task = _find_task(tasks, "Sync the cluster_data directory to s3_bucketname")
        params = task["community.aws.s3_sync"]
        assert "*.pem" in params.get("exclude", "").split(",")


class TestTaskParametersTheBuildDependsOn:
    """The `delete_*` gate walk below checks which *variables* gate a task. These
    check individual task *parameters* — a class the walk cannot see, and where
    three separate mutations survived the whole suite."""

    def test_monitoring_tarball_download_is_checksum_verified(self):
        """The tarball is fetched from GitHub at build time and staged to S3, so
        the download is the one point where a moved, corrupted, or tampered
        artifact enters the cluster. Deleting `checksum:` restores exactly the
        unverified-download behavior the S3 staging exists to avoid."""
        task, _ = _find_leaf(
            "create_pcluster.yml", "Download aws-parallelcluster-monitoring tarball from GitHub"
        )
        params = task["get_url"]
        assert params.get("checksum") == "{{ monitoring_version_checksum }}", (
            "the monitoring tarball download must be checksum-verified against "
            "monitoring_version_checksum"
        )
        assert "github.com" in params["url"], "unexpected download source"

    def test_monitoring_checksum_variable_is_threaded_end_to_end(self):
        """A checksum: line that references an undefined variable is worse than
        none — vars_file.j2 renders under StrictUndefined, so the build dies at
        template time. Both halves of the pair must be present."""
        with open(os.path.join(REPO_ROOT, "templates", "vars_file.j2")) as fh:
            vars_template = fh.read()
        with open(os.path.join(REPO_ROOT, "make_pcluster.py")) as fh:
            maker = fh.read()
        assert "monitoring_version_checksum" in vars_template
        assert "monitoring_version_checksum" in maker

    def test_grafana_ssm_parameter_path_matches_everywhere_it_is_used(self):
        """The teardown deletes the Grafana admin password by literal path. A typo
        there leaks the parameter on every teardown, and the gate assertion below
        still passes because the gate itself is untouched. Cross-check the
        deleter against every other place the path is written or printed."""
        expected = "/parallelcluster/{{ cluster_name }}/grafana/admin-password"
        task, _ = _find_leaf("delete_pcluster.yml", "Delete Grafana SSM password parameter")
        assert task["community.aws.ssm_parameter"]["name"] == expected

        suffix = "/grafana/admin-password"
        for relpath in (
            os.path.join("make_pcluster.py"),
            os.path.join("templates", "grafana_tunnel.j2"),
            os.path.join("src", "create_pcluster.yml"),
        ):
            with open(os.path.join(REPO_ROOT, relpath)) as fh:
                assert suffix in fh.read(), (
                    f"{relpath} no longer references {suffix} — the teardown "
                    f"deleter and the readers have diverged"
                )

    def test_every_benchmark_task_is_gated_on_enable_hpc_benchmarks(self):
        """enable_hpc_benchmarks=false must mean no benchmark work at all: no
        staging, no S3 upload, no remote mkdir. Dropping the gate off a single
        task survived the suite, and one task (the head node mkdir) had in fact
        shipped without it."""
        for playbook in ("create_pcluster.yml", "delete_pcluster.yml"):
            for name, task, conds in _leaf_tasks(os.path.join(REPO_ROOT, "src", playbook)):
                if not name or not re.search(r"performance|benchmark", name, re.I):
                    continue
                # set_fact tasks that only initialize an empty list touch nothing.
                if set(task) <= {"name", "set_fact", "when"} and not any(
                    task["set_fact"].values()
                ):
                    continue
                assert any("enable_hpc_benchmarks" in c for c in conds), (
                    f"{playbook}: task {name!r} does benchmark work but nothing in "
                    f"its `when:` chain gates on enable_hpc_benchmarks: {conds}"
                )

    def test_every_monitoring_task_is_gated_on_enable_monitoring(self):
        """The mirror of the benchmark gate. A monitoring task that runs with
        monitoring disabled fails on a cluster that has no monitoring stack."""
        for playbook in ("create_pcluster.yml", "delete_pcluster.yml"):
            for name, task, conds in _leaf_tasks(os.path.join(REPO_ROOT, "src", playbook)):
                if not name or not re.search(r"monitoring|grafana", name, re.I):
                    continue
                assert any("enable_monitoring" in c for c in conds), (
                    f"{playbook}: task {name!r} does monitoring work but nothing in "
                    f"its `when:` chain gates on enable_monitoring: {conds}"
                )


def _teardown_extra_vars():
    """The keys kill_pcluster.py actually hands to the teardown playbook."""
    with open(os.path.join(REPO_ROOT, "kill_pcluster.py")) as fh:
        source = fh.read()
    block = source.split("_destroy_extra_vars_str = json.dumps(", 1)[1]
    return set(re.findall(r'^\s*"([a-z_0-9]+)":', block.split("\n    )", 1)[0], re.M))


def _when_expressions(playbook_path):
    """Every `when:` expression in the playbook, scalar and list forms alike."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "when":
                    out.extend(value if isinstance(value, list) else [str(value)])
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    with open(playbook_path) as fh:
        walk(yaml.safe_load(fh))
    return out


def test_teardown_flags_reach_the_playbook_that_consumes_them():
    """--delete_fsx was a dead flag: it was threaded all the way into the playbook
    but its only consumer was a `when:` on the hydration-policy detach, so
    `--delete_fsx=false` did not preserve the filesystem (PCluster fixes FSx
    DeletionPolicy to Delete at creation time) and instead orphaned an IAM policy
    on a role queued for deletion. A teardown flag the operator can set must
    actually gate teardown behavior."""
    passed = _teardown_extra_vars()
    assert passed, "could not parse the teardown extra-vars block"

    with open(os.path.join(REPO_ROOT, "src", "delete_pcluster.yml")) as fh:
        playbook = fh.read()

    # Consumed by Ansible itself or by kill_pcluster.py, not by a task condition:
    # debug_mode only raises ansible_verbosity to -vvv on the Python side.
    plumbing = {
        "ansible_python_interpreter",
        "cluster_name",
        "cluster_serial_number",
        "debug_mode",
    }
    for key in sorted(passed - plumbing):
        assert re.search(rf"\b{key}\b", playbook), (
            f"kill_pcluster.py passes {key!r} to delete_pcluster.yml but nothing reads it"
        )


def test_no_teardown_flag_advertises_control_it_does_not_have():
    """Every --delete_* flag on kill_pcluster.py must be passed through to the
    teardown playbook. A flag that argparse accepts but never forwards silently
    does nothing, which is how --delete_fsx read as an FSx preservation switch."""
    with open(os.path.join(REPO_ROOT, "kill_pcluster.py")) as fh:
        source = fh.read()

    flags = {m.lstrip("-") for m in re.findall(r'"--(delete_[a-z_0-9]+)"', source)}
    assert flags, "no --delete_* flags found in kill_pcluster.py"

    passed = _teardown_extra_vars()
    orphans = sorted(flags - passed)
    assert not orphans, (
        f"--delete_* flags never forwarded to the teardown playbook: {orphans}"
    )


def test_teardown_playbook_gates_only_on_variables_it_is_given():
    """The mirror of the two checks above, and the one that actually catches the
    --delete_fsx shape: a `when:` may reference delete_fsx with a `| default()`
    fallback, which renders cleanly whether or not anything passes the variable.
    The gate then looks operator-controllable while being permanently inert.
    Every delete_* variable a teardown condition reads must be supplied."""
    conditions = _when_expressions(os.path.join(REPO_ROOT, "src", "delete_pcluster.yml"))
    assert conditions, "no when: conditions parsed out of delete_pcluster.yml"

    referenced = set(re.findall(r"\b(delete_[a-z_0-9]+)\b", " ".join(conditions)))
    assert referenced, "no delete_* variables gated on in delete_pcluster.yml"

    passed = _teardown_extra_vars()
    ungated = sorted(referenced - passed)
    assert not ungated, (
        f"delete_pcluster.yml gates on delete_* variables kill_pcluster.py never "
        f"passes, so the gate can never be false: {ungated}"
    )
