"""A local MariaDB on the head node so `sacct` reports job history.

Opt-in via `--enable_slurm_accounting`, default false, because it is not
free: it adds a database to a node that already runs slurmctld, and the
accounting dies with the cluster.

**Every step is non-fatal, and that is the design rather than politeness.**
Once slurm.conf names an accounting host, slurmctld blocks indefinitely
when that host does not answer -- observed live on a test cluster, where
slurmdbd died with the session that started it and `sinfo` hung until
slurm.conf was restored from backup. So a failure anywhere must leave the
cluster exactly as it would have been without the feature.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _postinstall(params, _unused=None):
    """Render postinstall.j2 the way ansible.builtin.template does.

    trim_blocks=True / lstrip_blocks=False are Ansible's own defaults, read
    out of the installed package rather than assumed -- with both off,
    every {% if %} leaves a blank line the node never sees.
    """
    import jinja2

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(REPO_ROOT, "templates")),
        undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env.get_template("postinstall.j2").render(**params)


def _uncommented(text):
    """Source with comment lines removed.

    Several rules below are enforced by *absence* -- no `stat -c %U`, no
    `sacctmgr add cluster`, no ArchiveDir -- and the block explains each
    one in a comment that names the very thing being banned. Matching the
    raw text makes those tests fail on a correct implementation, and the
    obvious way to make them pass is to delete the explanation.
    """
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


class TestItIsOffByDefault:
    def test_the_default_is_false(self):
        from pcluster_core import MAKE_CLUSTER_DEFAULTS

        assert MAKE_CLUSTER_DEFAULTS["enable_slurm_accounting"] == "false"

    def test_it_is_a_real_bool_on_the_params(self):
        """The whole `"false"` is truthy class of bug: a string default on a
        bool-annotated field means every feature reads as enabled."""
        import typing

        from pcluster_core import MakeClusterParams

        assert typing.get_type_hints(MakeClusterParams)["enable_slurm_accounting"] is bool

    def test_the_cli_offers_only_true_or_false(self):
        src = open(os.path.join(REPO_ROOT, "make_pcluster.py")).read()
        m = re.search(r'"--enable_slurm_accounting",\s*\n\s*choices=\[([^\]]*)\]', src)
        assert m, "the flag is not defined with explicit choices"
        assert "true" in m.group(1) and "false" in m.group(1)

    def test_nothing_renders_when_it_is_off(self, cluster_params):
        body = _postinstall(cluster_params)
        assert "mariadb" not in body.lower()
        assert "slurmdbd" not in body.lower()


class TestNothingHereCanFailTheNode:
    """postinstall runs under `set -euo pipefail` on every node type. A
    non-zero exit fails the node's bootstrap, and for a compute node
    clustermgtd relaunches it toward the partition's ten-failure
    protected-mode threshold."""

    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    def test_the_whole_block_is_head_node_gated(self, body):
        """slurmdbd runs on the head node and the database is reached over
        localhost. A compute node installing MariaDB is pure latency at
        best, and N nodes racing on shared paths at worst.

        The gate must be the *nearest* preceding conditional, not merely
        one somewhere above: this file has several head-node gates, and
        searching backwards for any of them passes even when the
        accounting block's own gate has been removed."""
        lines = body.splitlines()
        i = next(n for n, l in enumerate(lines)
                 if "Slurm accounting: installing MariaDB" in l)
        opener = next((l.strip() for l in reversed(lines[:i])
                       if l.strip().startswith(("if ", "elif "))), None)
        assert opener == 'if [ "$NODE_TYPE" == "HeadNode" ]', (
            f"the nearest enclosing conditional is {opener!r}, not the "
            f"head-node gate"
        )

    @pytest.mark.parametrize("step", [
        "apt-get -y install mariadb-server",
        "systemctl restart mariadb",
        "CREATE DATABASE IF NOT EXISTS slurm_acct_db",
        "systemctl enable --now slurmdbd",
    ])
    def test_every_install_step_tolerates_its_own_failure(self, body, step):
        i = body.index(step)
        window = body[i:i + 700]
        assert "||" in window, f"{step!r} has no failure branch"
        assert "WARNING" in window or "_acct_ok=0" in window

    def test_a_failure_turns_the_feature_off_rather_than_continuing(self, body):
        """_acct_ok is the latch. Without it a failed MariaDB install would
        fall through to writing slurmdbd.conf and editing slurm.conf."""
        assert body.count("_acct_ok=0") >= 4
        assert body.count('if [ "$_acct_ok" -eq 1 ]') >= 3


class TestSlurmConfIsNotTouchedDuringTheBootstrap:
    """The failure that cost a whole build.

    postinstall runs as OnNodeConfigured, *inside* PCluster's own
    bootstrap: `aws-parallelcluster-slurm::finalize_head_node` has not run
    yet and it waits for clustermgtd to write its heartbeat. Restarting
    slurmctld from postinstall tore the scheduler out from under that
    recipe --

        cat: /opt/slurm/etc/pcluster/.slurm_plugin/clustermgtd_heartbeat:
        No such file or directory

    -- and CREATE_FAILED. The accounting block had *succeeded*; it was its
    success at the wrong moment that broke the cluster. Same class as the
    GPU NVMe block having to skip devices the cookbook already claimed.
    """

    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    def test_postinstall_never_restarts_slurmctld(self, body):
        """The single property that would have caught it. postinstall may
        install and start its own services; slurmctld belongs to the
        cookbook until the bootstrap is done."""
        inline = body.split("ACCTDEFER")[0] + body.split("ACCTDEFER")[-1]
        assert "systemctl restart slurmctld" not in inline, (
            "postinstall restarts slurmctld inline, which fails "
            "finalize_head_node and the whole build"
        )

    def test_postinstall_never_edits_slurm_conf(self, body):
        inline = body.split("ACCTDEFER")[0] + body.split("ACCTDEFER")[-1]
        assert "AccountingStorageType" not in inline

    def test_the_work_is_deferred_to_a_unit(self, body):
        assert "/etc/systemd/system/pcm-slurm-acct.service" in body
        assert "Type=oneshot" in body

    def test_the_deferred_step_waits_for_the_cookbooks_own_signal(self, body):
        """clustermgtd's heartbeat is the cookbook saying it has finished.
        Waiting on a timer instead would be a guess about how long a
        bootstrap takes."""
        deferred = body.split("ACCTDEFER")[1]
        assert "clustermgtd_heartbeat" in deferred
        i = deferred.index("clustermgtd_heartbeat")
        j = deferred.index("AccountingStorageType")
        assert i < j, "slurm.conf is edited before the heartbeat is seen"
        # It must *wait*, not check once. The unit starts while the
        # bootstrap is still running, so a single check always finds
        # nothing and accounting silently never turns on -- a mutation
        # that deleted the loop passed an earlier version of this test,
        # because the one-shot check left the string in place.
        assert re.search(r"for .* in \$\(seq 1 \d+\); do", deferred), (
            "the heartbeat is checked once rather than waited for"
        )
        assert re.search(r"(?m)^\s*sleep \d+\s*$", deferred), "no backoff in the wait"

    def test_the_deferred_step_still_checks_the_port(self, body):
        deferred = body.split("ACCTDEFER")[1]
        assert ":6819 " in deferred
        assert deferred.index(":6819 ") < deferred.index("AccountingStorageType")

    def test_it_gives_up_rather_than_editing_blindly(self, body):
        deferred = body.split("ACCTDEFER")[1]
        assert deferred.count("leaving slurm.conf alone") == 2

    def test_starting_the_unit_does_not_block_the_bootstrap(self, body):
        """It waits for a heartbeat that only appears after postinstall
        returns, so starting it synchronously would deadlock."""
        assert "systemctl start --no-block pcm-slurm-acct.service" in body

    def test_slurmdbd_is_a_managed_unit_ordered_after_mariadb(self, body):
        assert "/etc/systemd/system/slurmdbd.service" in body
        unit = body[body.index("[Unit]"):body.index("SLURMDBDUNIT", body.index("[Unit]"))]
        assert "After=network-online.target mariadb.service" in unit
        assert "Requires=mariadb.service" in unit
        assert "Restart=on-failure" in unit


class TestTheDatabaseIsTunedForACoResidentNode:
    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    @pytest.mark.parametrize("setting,value", [
        ("performance_schema", "OFF"),
        ("max_connections", "24"),
        ("innodb_lock_wait_timeout", "900"),
        ("loose-innodb_snapshot_isolation", "OFF"),
        ("max_allowed_packet", "16M"),
        ("bind-address", "127.0.0.1"),
    ])
    def test_the_measured_values_are_the_ones_shipped(self, body, setting, value):
        """Measured on a live c5.xlarge head node: mariadbd 100 MB RSS,
        slurmdbd 12 MB, against 7.7 GB total -- less than the CloudWatch
        agent already running there. performance_schema is the single
        biggest win. The buffer pool and redo log are absent from this list
        on purpose: they are derived from the node's own memory, and
        TestTheSizesAreDerivedFromTheNode executes that derivation."""
        assert re.search(rf"(?m)^{re.escape(setting)}\s*=\s*{re.escape(value)}\s*$", body), setting

    def test_slurm_requires_both_of_its_named_settings(self, body):
        """Slurm's accounting documentation names innodb_lock_wait_timeout
        (900, for extended rollup queries) and innodb_snapshot_isolation
        (OFF) explicitly. With snapshot isolation ON a locking read fails
        with ER_CHECKREAD when another transaction touches the row, and
        upstream is unambiguous that slurmdbd cannot recover -- it stops."""
        assert re.search(r"(?m)^innodb_lock_wait_timeout\s*=\s*900\s*$", body)
        assert re.search(r"(?m)^loose-innodb_snapshot_isolation\s*=\s*OFF\s*$", body)

    def test_the_snapshot_isolation_setting_carries_the_loose_prefix(self, body):
        """MariaDB refuses to start on an unknown option in a config file,
        and this variable does not exist before 10.6.18 / 10.11.8. Without
        the prefix, enabling accounting on an older MariaDB takes the
        database down at startup -- which takes the scheduler with it, since
        slurmctld blocks on a slurmdbd that cannot reach its database."""
        assert "loose-innodb_snapshot_isolation" in body
        assert not re.search(r"(?m)^innodb_snapshot_isolation\s*=", body)

    def test_the_database_does_not_listen_on_the_network(self, body):
        """slurmdbd is on the same node. Not listening is a stronger bound
        than a security group rule."""
        assert re.search(r"(?m)^bind-address\s*=\s*127\.0\.0\.1\s*$", body)

    def test_binary_logging_is_off(self, body):
        """No replication and no point-in-time recovery -- this database
        dies with the head node by design, so the binary log is pure write
        amplification."""
        assert re.search(r"(?m)^skip-log-bin\s*$", body)

    def test_the_password_is_generated_and_not_left_in_the_environment(self, body):
        assert "openssl rand" in body
        assert "unset _acct_pw" in body
        assert "0o600" not in body and "chmod 600 /opt/slurm/etc/slurmdbd.conf" in body


class TestBothPackageFamiliesAreCovered:
    def test_ubuntu_uses_apt(self, cluster_params_slurm_accounting):
        body = _postinstall(cluster_params_slurm_accounting)
        assert "apt-get -y install mariadb-server" in body
        assert "dnf -y install" not in body

    def test_the_dnf_family_gets_its_own_package_name(
            self, cluster_params_slurm_accounting_rhel):
        """AL2023 and RHEL 9 package it as mariadb105-server; the fallback
        to mariadb-server covers distributions that do not."""
        body = _postinstall(cluster_params_slurm_accounting_rhel)
        assert "dnf -y install mariadb105-server" in body
        assert "apt-get" not in body


class TestTheTuningFileIsActuallyApplied:
    """Writing the config is not applying it.

    Measured on a live head node: the file was written at 02:52:37 and
    MariaDB had started at 02:52:33, because installing `mariadb-server`
    starts the service from the package's own postinst. `systemctl enable
    --now mariadb` against an already-running unit is a no-op, so every
    value in the file was ignored -- and the two that read back correctly
    were coincidences where the setting happened to equal the stock
    default, which is exactly what made it look like it had worked.
    """

    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    def test_mariadb_is_restarted_after_the_config_is_written(self, body):
        cfg = body.index("99-slurm-acct.cnf")
        restart = body.index("systemctl restart mariadb")
        assert restart > cfg, "the restart must follow the config write"

    def test_enable_now_is_not_relied_on_to_apply_the_config(self, body):
        """`enable --now` is still used to make the unit persist across a
        reboot, but it must not be the only thing between writing the file
        and expecting it to be in force."""
        assert "systemctl enable --now mariadb" not in body

    def test_the_effective_value_is_read_back(self, body):
        """A test that only checks the file exists is the same test that
        passed while nothing in it applied. The node asks the server."""
        assert "@@innodb_buffer_pool_size" in body
        i = body.index("@@innodb_buffer_pool_size")
        assert "WARNING" in body[i:i + 600]


class TestTheSizesAreDerivedFromTheNode:
    """The pool and the redo log scale with the head node's memory.

    Slurm's own guidance -- 5-50% of memory, at least 4 GiB, log at 25% of
    pool -- is sizing for a dedicated SQL server. Ours is co-resident with
    slurmctld and holds one cluster purged at a month, so both ends are
    bounded: the pool ceiling is Slurm's 4 GiB minimum, and the log ceiling
    is 256M, because 25% of a 4096M pool is a 1024M redo log preallocated
    on disk for a database measured at ~97 MB.
    """

    @pytest.fixture
    def snippet(self, cluster_params_slurm_accounting):
        body = _postinstall(cluster_params_slurm_accounting)
        start = body.index('_mem_mb="$(awk')
        end = body.index('if [ "$_log_mb" -gt 256 ]')
        return body[start:body.index("\n", end) + 1]

    @pytest.mark.parametrize("mem_kb,pool,log", [
        (1048576, 128, 32),      # 1 GiB  -- the floor binds
        (8388608, 819, 204),     # 8 GiB  -- neither cap binds
        (16777216, 1638, 256),   # 16 GiB -- the log cap binds
        (67108864, 4096, 256),   # 64 GiB -- both caps bind
    ])
    def test_the_derivation_is_run_not_matched(self, snippet, tmp_path, mem_kb, pool, log):
        """Executed under real bash against a fake /proc/meminfo. A source
        match cannot tell a correct formula from one whose bounds are
        transposed, and both bounds are plain integers."""
        import subprocess

        meminfo = tmp_path / "meminfo"
        meminfo.write_text(f"MemTotal:       {mem_kb} kB\nMemFree:  1 kB\n")
        script = (
            "set -euo pipefail\n"
            + snippet.replace("/proc/meminfo", str(meminfo))
            + 'echo "$_pool_mb $_log_mb"\n'
        )
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == [str(pool), str(log)]

    def test_the_bounds_survive_set_e(self, snippet):
        """`[ cond ] && var=x` returns 1 when cond is false, and this script
        runs under `set -euo pipefail`, so the shorthand kills the node on
        every size where the bound does not bind -- which is most of them.
        The 8 GiB case above is what exercises it; this pins the shape so
        the shorthand cannot come back on a path no size reaches."""
        for line in snippet.splitlines():
            if line.strip().startswith("[") and "&&" in line:
                pytest.fail(f"conditional assignment under set -e: {line.strip()}")

    def test_the_pool_is_written_from_the_derivation(self, cluster_params_slurm_accounting):
        body = _postinstall(cluster_params_slurm_accounting)
        assert re.search(r"(?m)^innodb_buffer_pool_size\s*=\s*\$\{_pool_mb\}M\s*$", body)
        assert re.search(r"(?m)^innodb_log_file_size\s*=\s*\$\{_log_mb\}M\s*$", body)

    def test_the_config_heredoc_is_unquoted_so_the_sizes_interpolate(self, cluster_params_slurm_accounting):
        """A quoted heredoc delimiter would ship the literal string
        ${_pool_mb}M to MariaDB, which refuses to start on it -- taking the
        scheduler down with it. The delimiter must be bare."""
        body = _postinstall(cluster_params_slurm_accounting)
        assert "<<ACCTCNF" in body
        assert "<<'ACCTCNF'" not in body


class TestSlurmUserIsReadFromSlurmConf:
    """The one that built a green cluster with a dead scheduler.

    It shipped as `stat -c %U /opt/slurm/etc/slurm.conf`, which returns
    root because root owns that file, while slurmctld actually runs as
    `slurm` (uid 401, confirmed on a live node). slurmdbd started happily
    with SlurmUser=root and then refused slurmctld with "Your user doesn't
    have privilege to perform this action" -- so the stack reached
    CREATE_COMPLETE and every Slurm command was dead.
    """

    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    def test_the_owner_of_the_file_is_never_consulted(self, body):
        assert "stat -c %U" not in _uncommented(body)

    def test_it_comes_from_the_slurm_user_line(self, body):
        assert re.search(r"awk -F=\s*'/\^SlurmUser=/", body)

    def test_it_falls_back_to_slurm_not_root(self, body):
        i = body.index("_slurm_user=")
        window = body[i:i + 400]
        assert "_slurm_user=slurm" in window
        assert "_slurm_user=root" not in window


class TestTheDeferredStepRollsBackWhatItBreaks:
    """A cluster without accounting works. A cluster whose slurmctld cannot
    reach slurmdbd is one where every Slurm command hangs -- so the edit to
    slurm.conf is reverted unless the scheduler comes back *answering*, not
    merely active."""

    @pytest.fixture
    def deferred(self, cluster_params_slurm_accounting):
        body = _postinstall(cluster_params_slurm_accounting)
        start = body.index("#!/bin/bash\n# Enable Slurm accounting once")
        return body[start:body.index("\nACCTDEFER")]

    def test_a_backup_is_taken_before_the_edit(self, deferred):
        assert deferred.index('cp "$CONF" "$BACKUP"') < deferred.index("AccountingStorageType=accounting_storage/slurmdbd")

    def test_every_failure_path_rolls_back(self, deferred):
        assert deferred.count("_rollback ") >= 3, "restart, is-active and sinfo all need one"
        assert 'cp "$BACKUP" "$CONF"' in deferred

    def test_an_active_slurmctld_is_not_accepted_as_a_working_one(self, deferred):
        """slurmctld can be active and still not answer -- that is exactly
        the hang this feature caused once already."""
        assert "is-active" in deferred
        assert "sinfo" in deferred

    @pytest.mark.parametrize("sinfo_rc,expect_rollback", [(0, False), (1, True)])
    def test_the_rollback_actually_runs(self, deferred, tmp_path, sinfo_rc, expect_rollback):
        """Executed against stubbed systemctl/ss/sinfo with real files, so
        the assertion is on what happened to slurm.conf rather than on the
        presence of a function definition."""
        import subprocess

        conf = tmp_path / "slurm.conf"
        conf.write_text("ClusterName=probe\nSlurmUser=slurm\n")
        hb = tmp_path / "heartbeat"
        hb.write_text("beat\n")
        stub = tmp_path / "bin"
        stub.mkdir()
        for name, rc in (("systemctl", 0), ("ss", 0), ("runuser", 0), ("timeout", sinfo_rc)):
            p = stub / name
            body = "#!/bin/bash\n"
            if name == "ss":
                body += "echo 'LISTEN 0 128 127.0.0.1:6819 0.0.0.0:*'\n"
            if name == "runuser":
                body += "echo probe\n"
            p.write_text(body + f"exit {rc}\n")
            p.chmod(0o755)

        script = (
            deferred.replace("/opt/slurm/etc/slurm.conf.pcm-preacct", str(tmp_path / "backup"))
            .replace("/opt/slurm/etc/slurm.conf", str(conf))
            .replace("/opt/slurm/etc/pcluster/.slurm_plugin/clustermgtd_heartbeat", str(hb))
        )
        env = dict(os.environ, PATH=f"{stub}:/usr/bin:/bin")
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        assert out.returncode == 0, out.stderr

        rolled_back = "AccountingStorageType" not in conf.read_text()
        assert rolled_back is expect_rollback, out.stderr

    def test_the_harness_can_tell_the_two_apart(self, deferred, tmp_path):
        """Vacuity guard: if both parametrizations produced the same file,
        the test above would pass against a script with no rollback at
        all."""
        assert 'cp "$BACKUP" "$CONF"' in deferred


class TestTheClusterRegistersItself:
    def test_no_cluster_is_added_by_hand(self, cluster_params_slurm_accounting):
        """Slurm 20.02 and later register the cluster when slurmctld starts
        against slurmdbd. Adding the row first is what produced "there's no
        TRES from it" on a live build -- a cluster row created before
        slurmctld had reported its resources."""
        body = _postinstall(cluster_params_slurm_accounting)
        assert not re.search(r"sacctmgr[^\n]*add cluster", _uncommented(body))

    def test_the_users_are_added_as_admins(self, cluster_params_slurm_accounting):
        """Without AdminLevel=Admin, sacct shows a user only their own
        jobs, which defeats the point of running it from the MCP tools."""
        body = _postinstall(cluster_params_slurm_accounting)
        assert re.search(r"sacctmgr[^\n]*add user[^\n]*AdminLevel=Admin", body)

    def test_a_missing_owner_account_is_skipped_not_fatal(self, cluster_params_slurm_accounting):
        """cluster_owner need not be a Linux user on the node."""
        body = _postinstall(cluster_params_slurm_accounting)
        assert re.search(r'id "\$u" >/dev/null 2>&1 \|\| continue', body)


class TestThePurgePolicyIsSet:
    """Every Purge* setting defaults to never, so a database with no policy
    grows without bound on a root volume sized for an operating system."""

    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    @pytest.mark.parametrize("key,value", [
        ("PurgeEventAfter", "1month"),
        ("PurgeJobAfter", "1month"),
        ("PurgeResvAfter", "1month"),
        ("PurgeStepAfter", "1month"),
        ("PurgeSuspendAfter", "1month"),
        ("PurgeTXNAfter", "12months"),
        ("PurgeUsageAfter", "12months"),
    ])
    def test_each_purge_setting_is_named(self, body, key, value):
        assert re.search(rf"(?m)^{key}={value}\s*$", body)

    def test_nothing_is_archived(self, body):
        """ArchiveDir defaults to /tmp, and an archive nobody collects
        before the head node is torn down is not a backup."""
        assert "ArchiveDir" not in _uncommented(body)

    def test_the_rolled_up_usage_outlives_the_job_detail(self, body):
        """Reports are built from the usage rollup, so purging it on the
        same one-month schedule as the job rows would leave the reports
        empty while the database looked healthy."""
        assert re.search(r"(?m)^PurgeJobAfter=1month\s*$", body)
        assert re.search(r"(?m)^PurgeUsageAfter=12months\s*$", body)


class TestTheDaemonsAgreeOnPortsAndAuth:
    @pytest.fixture
    def body(self, cluster_params_slurm_accounting):
        return _postinstall(cluster_params_slurm_accounting)

    @pytest.mark.parametrize("line", [
        "AuthType=auth/munge",
        "DbdPort=6819",
        "StoragePort=3306",
        "AccountingStoragePort=6819",
        "JobAcctGatherFrequency=30",
    ])
    def test_each_is_stated_rather_than_defaulted(self, body, line):
        assert re.search(rf"(?m)^\s*\"?{re.escape(line)}\"?,?\s*$", body), line

    def test_slurmdbd_waits_for_munge(self, body):
        """AuthType=auth/munge means slurmdbd cannot authenticate anything
        before munge is up, and systemd will happily start it first."""
        i = body.index("Description=Slurm DBD accounting daemon")
        unit = body[i:i + 400]
        assert "munge.service" in unit
