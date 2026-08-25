"""MCP tool wrappers over src/pcluster_core.py's core_* functions.

Every wrapper here is a thin adapter: resolve state into the typed inputs a
core function already takes, call it, and return a JSON-serializable dict.
No business logic lives in this file -- that was the entire point of
Workstream 1's core/shim split, and duplicating any of it here would create
exactly the two-sources-that-can-disagree problem CLAUDE.md documents
elsewhere.

The local/remote split is data, not duplicated code. _LOCAL_ONLY names the
tools that must never be registered on the remote transport, and
register_tools() consults it. The plan's reasoning for each exclusion, kept
here because it is the sort of thing that reads as arbitrary later:

  * rotate_cluster_key -- not because the return value leaks key material
    (it deliberately does not; core_rotate_cluster_key returns status and
    paths only, never bytes), but because the operation is useless
    remotely: it writes a local .pem the operator later needs, and Lambda's
    /tmp is ephemeral and never reachable from a Claude web session. The
    "obvious fix" of returning the key content in the response is precisely
    the exposure this exclusion exists to prevent.
  * manage_grafana_tunnel -- an SSH local port forward is only meaningful
    when "local" means the caller's own machine, which a remote dispatcher
    categorically is not. There is no safely-useful remote version.
  * access_cluster -- same reasoning: it execs an interactive ssh session.
  * apply_queue_config -- the only exclusion that is a hard limit rather
    than a judgment. It blocks for up to ~30 minutes across three causally
    dependent phases, and every remote tool runs in a Lambda whose maximum
    function timeout is 900 seconds (a service ceiling, not a default; see
    mcp_server/deploy.py). Past it the function is killed mid-operation,
    with the fleet already stopped, a stack update already in flight, and
    the cluster's S3 lock held by a process that no longer exists -- a
    partial mutation, not a clean failure. Remote callers get the three
    phases instead (stop_fleet, apply_cluster_update, start_fleet), each
    non-blocking, which is what core_apply_cluster_update was split out
    of core_apply_queue_config to make possible.
"""

import contextlib
import dataclasses
import functools
import hashlib
import json
import os

from pcluster_core import (
    ClusterRecord,
    MAKE_CLUSTER_DEFAULTS,
    PClusterMakerError,
    _read_cluster_record,
    _validate_cluster_name,
    core_add_queue,
    core_apply_cluster_update,
    core_apply_queue_config,
    build_make_cluster_params,
    core_check_cluster_health,
    core_create_cluster,
    core_delete_cluster,
    core_finalize_cluster_build,
    core_diagnose_cluster,
    core_get_cost_report,
    core_list_clusters,
    core_list_queues,
    core_manage_grafana_tunnel,
    core_remove_queue,
    core_resolve_access_node_type,
    core_rotate_cluster_key,
    list_cluster_records,
    load_cluster_defaults,
    resolve_region_from_az,
    core_start_fleet,
    core_stop_fleet,
)

from pcluster_core import (
    _derive_locks_bucket,
    _acquire_distributed_cluster_lock,
    _derive_locks_bucket,
    s3_release_cluster_lock,
)

from mcp_server.confirmation_token import mint, verify

from mcp_server.tiers import TOOL_TIERS

_LOCAL_ONLY = frozenset({
    # Writes access scripts into the operator's active_clusters/ and scp's
    # a local staging tree with the local .pem -- a remote handler has
    # neither, and the scripts it renders are only useful on the machine
    # that will run them. Same reasoning as rotate_cluster_key.
    "finalize_cluster_build",
    "rotate_cluster_key",
    "manage_grafana_tunnel",
    "apply_queue_config",
})

# Cluster parameters the remote transport will not set, at the operator's
# direction. These are the only knobs that change *what code runs on the
# nodes* rather than what infrastructure gets built: the two install
# scripts run as root on every node, and a custom AMI replaces the whole
# node image. They stay CLI-only.
#
# A denylist rather than an omission from the schema, because `overrides`
# is an open dict -- there is no schema to leave them out of. The tool
# rejects them explicitly and says where to go instead, which is more
# useful than a silent drop and much more useful than a generic
# "unknown parameter".
_REMOTE_DENIED_PARAMS = {
    "pre_install_script": "runs as root on every node",
    "post_install_script": "runs as root on every node",
    "custom_ami": "replaces the entire node image",
}


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pcluster_bin():
    return os.path.join(_repo_root(), ".venv", "bin", "pcluster")


def _enumerate_cluster_names():
    """Every cluster this checkout tracks. Mirrors list_pcluster.py's own
    enumeration, including its tolerance for a directory whose name is not
    a valid cluster name -- skipped rather than raising, since an operator
    can leave arbitrary directories under active_clusters/."""
    root = os.path.join(_repo_root(), "active_clusters")
    if not os.path.isdir(root):
        return []
    names = []
    for entry in os.scandir(root):
        if not entry.is_dir(follow_symlinks=False):
            continue
        try:
            _validate_cluster_name(entry.name)
        except SystemExit:
            continue
        names.append(entry.name)
    return sorted(names)


def _store_region():
    """The region whose lock/record bucket this server reads.

    Lambda always sets AWS_REGION; locally it comes from the environment
    or the active profile. The bucket is per account+region because the
    lock already is, so a server sees the clusters built in its own
    region -- one built in another region is visible to a server there.
    Not a limitation to work around silently: cross-region discovery would
    mean scanning every region's bucket on every listing.
    """
    import boto3

    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
    )


def _record_store(region=None):
    """(s3 client, bucket) for the shared record store, or (None, None).

    `region` is the *cluster's* region and is what callers pass whenever
    they have a record in hand. That is the whole point: the bucket is
    per account+region, and everything that writes it -- core_create_cluster
    publishing, core_delete_cluster removing -- derives it from the
    cluster's region, as the S3 lock already does. Deriving it from the
    process environment instead meant a laptop with AWS_DEFAULT_REGION
    us-east-1 building into us-west-2 wrote the record to one bucket and
    looked for it in another, and teardown could never reach whatever the
    MCP path had written.

    _store_region() remains the fallback for the two cases with no cluster
    to ask: enumerating the store, and looking up a cluster this machine
    has no local record for. A server discovers its own region's clusters;
    cross-region discovery would mean scanning every region on every call.

    Returns (None, None) rather than raising whenever the store cannot be
    addressed -- no region, no credentials, an STS call that fails. Every
    caller treats that as "no store", which degrades to exactly the local
    behavior that existed before it.
    """
    import boto3

    region = region or _store_region()
    if not region:
        return None, None
    try:
        bucket = _derive_locks_bucket(
            aws_account_id=_aws_account_id(), region=region
        )
        return boto3.client("s3", region_name=region), bucket
    except Exception:
        return None, None


def _load_records():
    """Every readable cluster record, local and shared.

    A cluster whose vars file is missing or unreadable is skipped, matching
    list_pcluster.py -- one broken cluster must not fail a listing of all
    the others. Names from the shared store are unioned in, which is what
    lets a handler with no filesystem see anything at all; a store that
    cannot be reached contributes nothing and is not an error.
    """
    s3, bucket = _record_store()
    names = list(_enumerate_cluster_names())
    if s3 is not None:
        names += list_cluster_records(s3, locks_bucketname=bucket)
    records = []
    for name in sorted(set(names)):
        rec = _read_cluster_record(
            name, _repo_root(), s3=s3, locks_bucketname=bucket
        )
        if rec is None:
            continue
        records.append(ClusterRecord.from_dict(rec))
    return records


@functools.lru_cache(maxsize=1)
def _aws_account_id():
    """Cached for the life of the process -- on Lambda that means once per
    container, not once per tool call."""
    import boto3

    return boto3.client("sts").get_caller_identity()["Account"]


@contextlib.contextmanager
def _cluster_lock(cluster_name, region, command):
    """Hold the S3 distributed lock around a live-cluster mutation.

    Held at the wrapper layer rather than inside the core function, and
    that placement is the whole point. core_stop_fleet/core_start_fleet/
    core_apply_queue_config are also the CLI's code path, and the
    migration plan's standing constraint is that CLI behavior does not
    change -- adding a lock down there would make `stop_pcluster.py`
    during an in-flight build start failing fast, which is arguably a fix
    but is definitely a change. The plan's own wording for this tier is
    "acquire/release the distributed lock around the call", and around is
    exactly where it goes.

    core_create_cluster and core_delete_cluster already lock internally
    (Workstream 4, round 27), so create/delete tools must NOT be wrapped in
    this -- that would deadlock against their own acquisition.
    """
    import boto3

    s3 = boto3.client("s3", region_name=region)
    bucket = _derive_locks_bucket(aws_account_id=_aws_account_id(), region=region)
    try:
        _acquire_distributed_cluster_lock(
            s3, locks_bucketname=bucket, region=region,
            cluster_name=cluster_name, command=command,
        )
    except SystemExit as e:
        # _acquire_distributed_cluster_lock sys.exit()s on a held lock,
        # which is right for the CLI and wrong here twice over: SystemExit
        # is a BaseException, so the handler's `except Exception` (narrow
        # on purpose, so a Lambda timeout is not reported as a tool
        # failure) does not catch it -- and pcluster_core already
        # documents that an uncaught SystemExit inside a long-lived
        # FastMCP process kills the whole server rather than failing one
        # call. A held lock is an ordinary, expected condition; it must
        # reach the model as a normal tool error.
        raise PClusterMakerError(str(e) or f"cluster {cluster_name!r} is locked")
    try:
        yield
    finally:
        s3_release_cluster_lock(s3, locks_bucketname=bucket, cluster_name=cluster_name)


def _defaults_fingerprint(cluster_name):
    """Digest of the defaults file this build would apply, or None.

    `<cluster_name>_defaults.yml` is applied automatically, so the token
    has to cover it: resolution is a pure function of the explicit
    arguments and this file's contents, and a file edited inside the
    token's 15-minute window would otherwise build something other than
    what was previewed while the token still verified.

    Hashing the resolution's *input* rather than its output is deliberate.
    Binding the resolved MakeClusterParams would say the same thing, but
    only by building it -- and verify() must run before
    build_make_cluster_params so that a tokenless caller is refused as
    tokenless rather than told which parameter it got wrong, which is what
    makes the gate unreachable-around.
    """
    _, contents = load_cluster_defaults(cluster_name)
    if not contents:
        return None
    payload = json.dumps(contents, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _plain(result):
    """Coerce a core function's return value into something JSON-safe.

    Core functions return frozen dataclasses, plain dicts, or bare strings
    depending on age; the tool surface should not expose that difference.
    """
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    return result


def _require_record(cluster_name):
    # Wrapped, like every other caller of this validator. It sys.exit()s,
    # which is right for a CLI and fatal here: SystemExit is a
    # BaseException, so FastMCP's handling does not turn it into a tool
    # error -- it unwinds the server process instead. 13 of the 18 tools
    # reach this function, so an invalid cluster_name from any of them
    # took the whole session down. Same reasoning as _cluster_lock's
    # translation below, and as build_make_cluster_params/
    # _queue_config_path/discover_defaults_file, which all wrap it.
    try:
        _validate_cluster_name(cluster_name)
    except SystemExit as e:
        raise PClusterMakerError(str(e))
    # Local first with no store at all: a cluster this machine built is
    # answered from its own vars file, and its region then addresses the
    # bucket for every later store call. Only a cluster with no local
    # record falls back to the ambient region, which is the one case with
    # nothing better to ask.
    rec = _read_cluster_record(cluster_name, _repo_root())
    if rec is None:
        store_region = _store_region()
        s3, bucket = _record_store()
        rec = _read_cluster_record(
            cluster_name, _repo_root(), s3=s3, locks_bucketname=bucket
        )
        # A record that came out of the store must name the store's own
        # region: the bucket is per account+region and _publish_cluster_record
        # derives it from the cluster's region, so the two agree by
        # construction. They can only disagree if something published to the
        # wrong bucket -- the exact defect the cluster's-region addressing
        # rule was introduced to fix. Refusing here is not a policy choice:
        # every later store call in this request would address the bucket
        # this record was NOT found in, and on a Lambda whose IAM grants one
        # region that surfaces as an opaque AccessDenied instead. Scoped to
        # the store branch on purpose -- a *local* vars file may name any
        # region, which is the whole point of reading it first.
        if rec is not None and rec.get("region") and store_region \
                and rec["region"] != store_region:
            raise PClusterMakerError(
                f"Cluster {cluster_name!r} was found in the {store_region} "
                f"record store but its record says it runs in "
                f"{rec['region']}. One of the two is wrong -- a record is "
                f"published to the bucket for its own cluster's region, so "
                f"these cannot disagree on a healthy store. Inspect "
                f"s3://parallelclustermaker-locks-<account>-{store_region}"
                f"/vars/{cluster_name}.json before acting on it."
            )
    if rec is None:
        raise PClusterMakerError(
            f"No cluster named {cluster_name!r} is tracked here: no vars file "
            f"under active_clusters/, and no record in the shared store for "
            f"{_store_region() or 'an unresolved region'}. This server manages "
            f"one region by design -- the record store is per account+region, "
            f"so a cluster built in another region is visible only to a server "
            f"running there. If {cluster_name!r} exists elsewhere, use that "
            f"region's endpoint."
        )
    return ClusterRecord.from_dict(rec)


def register_tools(mcp, *, remote, tier=None):
    """Register the tool set on a FastMCP instance.

    remote=True omits every tool in _LOCAL_ONLY. Both instances are built
    from the same wrappers -- nothing prevents registering all of them,
    they simply are not both exposed, which is what makes the exclusion
    auditable rather than an emergent property of two separately-written
    files.

    tier, when given, additionally restricts registration to the tools
    that tier serves (mcp_server.tiers.TOOL_TIERS). Filtering at
    registration rather than registering everything and removing the
    excess: the removal approach needs a deprecated FastMCP API, and more
    importantly it means a tool briefly exists on an instance that must
    never expose it.
    """

    def tool(fn):
        if remote and fn.__name__ in _LOCAL_ONLY:
            return fn
        if tier is not None and TOOL_TIERS.get(fn.__name__) != tier:
            return fn
        mcp.tool(fn)
        return fn

    @tool
    def list_clusters(region: str | None = None, owner: str | None = None,
                      live: bool = False) -> list[dict]:
        """List the ParallelCluster stacks this checkout tracks.

        live=True additionally queries CloudFormation for each cluster's
        current status, which is slower but authoritative.
        """
        entries = core_list_clusters(
            cluster_records=_load_records(), pcluster_bin=_pcluster_bin(),
            region_filter=region, owner_filter=owner, live=live,
        )
        return [dataclasses.asdict(e) for e in entries]

    @tool
    def check_cluster_health(cluster_name: str) -> dict:
        """Run the health checks check_pcluster.py performs.

        ssh_available tracks the transport, and that distinction is the
        whole point rather than a detail. The SSH-dependent sub-checks
        need the cluster's private key, which never reaches the remote
        transport -- so there they report SKIP via the branch
        core_check_cluster_health already has, instead of failing. On the
        local stdio server the key is on disk, so they run.

        Hardcoding False here (as this shipped) silently downgraded the
        *local* tool below the CLI it wraps: `check_slurm` is the one
        check that separates "the cluster exists" from "the cluster can
        run work", and it was reporting SKIP on the one transport that
        could have run it.
        """
        result = core_check_cluster_health(
            cluster_record=_require_record(cluster_name),
            pcluster_bin=_pcluster_bin(), ssh_available=not remote,
        )
        return _plain(result)

    @tool
    def get_cost_report(owner: str | None = None, days: int = 30) -> dict:
        """Cost Explorer spend for tracked clusters over the last N days."""
        result = core_get_cost_report(
            cluster_records=_load_records(), owner_filter=owner, days=days,
        )
        return _plain(result)

    @tool
    def list_queues(cluster_name: str) -> list[dict]:
        """List the Slurm queues defined in a cluster's config.

        Annotated `list[dict]`, like list_clusters, because that is what it
        returns. It said `dict` while returning a list, and FastMCP
        validates structured content against the annotation -- so every
        call failed with "structured_content must be a dict or None",
        carrying the correct payload in the error text. No local test saw
        it: they call the wrapper directly, where the annotation is inert.
        """
        rec = _require_record(cluster_name)
        _s3, _bucket = _record_store(rec.region)
        result = core_list_queues(
            cluster_name=cluster_name, repo_root=_repo_root(),
            s3=_s3, locks_bucketname=_bucket,
        )
        return _plain(result)

    @tool
    def diagnose_cluster(cluster_name: str, hours: int = 24,
                         include_cloudwatch: bool = True) -> dict:
        """Collect diagnostics for a cluster: stack status, node states,
        recent job history, and CloudWatch logs.

        ssh_available tracks the transport for the same reason as
        check_cluster_health: sections 2-5 need the cluster's private key,
        which never reaches the remote transport, and
        core_diagnose_cluster already degrades those to SKIP there. The
        local server has the key on disk and runs them.
        """
        result = core_diagnose_cluster(
            cluster_record=_require_record(cluster_name),
            pcluster_bin=_pcluster_bin(), hours=hours,
            include_cloudwatch=include_cloudwatch, ssh_available=not remote,
        )
        return _plain(result)

    @tool
    def resolve_access_info(cluster_name: str, login_node: bool = False,
                            head_node: bool = False) -> dict:
        """Report which node an operator would connect to, and how.

        Deliberately does not open a session -- that is access_cluster,
        which is local-transport-only because it execs an interactive ssh.
        This returns the resolved target so a caller can see it.
        """
        # _require_record, not `_read_cluster_record(...) or {}`: the empty
        # dict made this answer *confidently* from no data -- a login-node
        # cluster resolved to HeadNode rather than erroring -- and it was
        # the one record reader never threaded to the store, so a cluster
        # only the store knows about looked untracked.
        rec = dataclasses.asdict(_require_record(cluster_name))
        node_type = core_resolve_access_node_type(
            rec, cluster_name,
            login_node_requested=login_node, head_node_requested=head_node,
        )
        return {"cluster_name": cluster_name, "node_type": _plain(node_type)}

    @tool
    def add_queue(cluster_name: str, queue_type: str, ec2_instance_type: str,
                  queue_name: str | None = None, capacity_type: str = "spot",
                  initial_size: int = 2, max_size: int = 8,
                  maintain_initial_size: bool = False) -> dict:
        """Add a Slurm queue to a cluster's config.

        Edits local/S3 config only -- it does not apply the change to the
        running cluster. apply_queue_config is the separate, deliberately
        separate step that does (stop fleet, update, restart).
        """
        rec = _require_record(cluster_name)
        _s3, _bucket = _record_store(rec.region)
        return _plain(core_add_queue(
            cluster_name=cluster_name, repo_root=_repo_root(),
            s3=_s3, locks_bucketname=_bucket,
            queue_type=queue_type, ec2_instance_type=ec2_instance_type,
            queue_name=queue_name, capacity_type=capacity_type,
            initial_size=initial_size, max_size=max_size,
            maintain_initial_size=maintain_initial_size,
        ))

    @tool
    def remove_queue(cluster_name: str, queue_name: str) -> dict:
        """Remove a Slurm queue from a cluster's config. Config-only, like
        add_queue -- apply_queue_config is what reaches the cluster."""
        rec = _require_record(cluster_name)
        _s3, _bucket = _record_store(rec.region)
        return _plain(core_remove_queue(
            cluster_name=cluster_name, repo_root=_repo_root(),
            s3=_s3, locks_bucketname=_bucket, queue_name=queue_name,
        ))

    @tool
    def stop_fleet(cluster_name: str, wait: bool = False) -> dict:
        """Stop a cluster's compute fleet. The head node keeps running.

        wait defaults False here, unlike the CLI's blocking `--wait`: a
        single MCP call cannot block for a fleet transition, so the caller
        polls check_cluster_health instead.
        """
        rec = _require_record(cluster_name)
        with _cluster_lock(cluster_name, rec.region, "mcp stop_fleet"):
            return _plain(core_stop_fleet(
                cluster_record=rec, region=rec.region,
                pcluster_bin=_pcluster_bin(), wait=wait,
            ))

    @tool
    def start_fleet(cluster_name: str, wait: bool = False) -> dict:
        """Start a cluster's compute fleet. See stop_fleet on wait."""
        rec = _require_record(cluster_name)
        with _cluster_lock(cluster_name, rec.region, "mcp start_fleet"):
            return _plain(core_start_fleet(
                cluster_record=rec, region=rec.region,
                pcluster_bin=_pcluster_bin(), wait=wait,
            ))

    @tool
    def apply_cluster_update(cluster_name: str,
                             config_path: str | None = None) -> dict:
        """Apply an updated configuration to a cluster whose fleet is
        already stopped. Phase 2 of three; see apply_queue_config.

        Kicked off without waiting, like create_cluster and delete_cluster:
        an update runs well past any single call's budget. Poll
        check_cluster_health, then call start_fleet once it is done.

        config_path is optional: omit it and the configuration comes from
        the shared store, which is the only thing a remote caller can name
        -- it has no filesystem to hold a path on, and a path returned by
        an earlier add_queue call belonged to a different container.

        The fleet must be stopped first -- pcluster rejects the update
        otherwise. That ordering is the caller's to enforce here, which is
        the point of exposing the phases separately: a failed apply must
        not be followed by a blind restart.
        """
        rec = _require_record(cluster_name)
        s3, bucket = _record_store(rec.region)
        with _cluster_lock(cluster_name, rec.region, "mcp apply_cluster_update"):
            return _plain(core_apply_cluster_update(
                cluster_name=cluster_name, config_path=config_path,
                region=rec.region, pcluster_bin=_pcluster_bin(), wait=False,
                s3=s3, locks_bucketname=bucket,
            ))

    @tool
    def apply_queue_config(cluster_name: str, config_path: str) -> dict:
        """Apply a queue-config change to a running cluster: stop the
        fleet, update the cluster, restart the fleet.

        Blocking, and the one tool here that is: its three phases are
        causally dependent -- update-cluster requires an already-stopped
        fleet, and the restart requires a finished update. Expect up to
        ~30 minutes.

        Local transport only, for that reason. Remote callers drive the
        three phases themselves: stop_fleet, apply_cluster_update,
        start_fleet, polling check_cluster_health between them.
        """
        rec = _require_record(cluster_name)
        with _cluster_lock(cluster_name, rec.region, "mcp apply_queue_config"):
            return _plain(core_apply_queue_config(
                cluster_record=rec, config_path=config_path,
                region=rec.region, pcluster_bin=_pcluster_bin(),
            ))

    def _reject_denied(overrides):
        """Refuse the CLI-only parameters with an actionable message.

        Scoped to `overrides` on purpose, and a `<cluster>_defaults.yml`
        that sets the same three is applied without complaint. The denial
        exists to stop a *caller* -- a model, over the network -- from
        choosing what code runs on the nodes; the defaults file is the
        operator's own artifact on the operator's own disk, the same trust
        level as the CLI, which is precisely where these three are allowed.
        Extending the check to file-sourced values would refuse every real
        operator's file, since `pre_install_script`/`post_install_script`
        are in `pcluster_defaults.yml` itself.

        Nothing reaches a handler this way: the file is gitignored and in no
        tier's `sources` list in packaging.py. If that ever changes, this is
        the decision to revisit first.
        """
        denied = sorted(set(overrides or {}) & set(_REMOTE_DENIED_PARAMS))
        if not denied:
            return
        lines = [
            f"  {k} -- {_REMOTE_DENIED_PARAMS[k]}" for k in denied
        ]
        raise PClusterMakerError(
            "These cluster parameters cannot be set over the remote MCP "
            "transport:\n" + "\n".join(lines) + "\n\n"
            "They are the only settings that change what code runs on the "
            "cluster's nodes, so they are deliberately CLI-only. Build the "
            "cluster from a shell in the repo instead, for example:\n"
            "  ./make_pcluster.py -N <cluster> -O <owner> -E <email> "
            "-A <az> --" + denied[0] + " <value>\n"
            "Every other parameter is available here."
        )

    @tool
    def preview_cluster_config(
        cluster_name: str, cluster_owner: str, cluster_owner_email: str,
        az: str, headnode_instance_type: str,
        overrides: dict | None = None,
    ) -> dict:
        """Show the resolved configuration a cluster would be built with,
        and mint the token create_cluster requires.

        `overrides` accepts any parameter make_pcluster.py accepts, with
        two guards: an unknown key is rejected rather than ignored, and a
        value whose type does not match the parameter's is rejected rather
        than converted (this codebase carries booleans as the strings
        "true"/"false", so a real bool would otherwise be accepted and then
        do nothing). Three parameters are CLI-only; see _REMOTE_DENIED_PARAMS.

        No AWS mutation. It does make one read-only EC2 call, to verify the
        AZ and resolve the region before minting a token -- a token is an
        assertion that this config was previewed, and a preview that cannot
        say which region it previewed is not one. The parameter resolution
        above it stays offline, so an unbuildable config still fails without
        reaching AWS at all.
        """
        _reject_denied(overrides)
        params = build_make_cluster_params(
            cluster_name=cluster_name, cluster_owner=cluster_owner,
            cluster_owner_email=cluster_owner_email, az=az,
            headnode_instance_type=headnode_instance_type,
            overrides=overrides,
        )
        resolved = dataclasses.asdict(params)
        _defaults_path, _file_defaults = load_cluster_defaults(cluster_name)
        # What the file actually contributed to this build. Keys it carries
        # for other entry points (delete_s3_bucketname) are not build
        # parameters and are dropped, matching what the resolution itself
        # ignores; keys an explicit override shadowed belong under
        # non_default_settings, not here.
        _accepted = set(resolved) | set(MAKE_CLUSTER_DEFAULTS)
        _from_file = {
            k: v for k, v in sorted(_file_defaults.items())
            if k in _accepted and k not in (overrides or {})
        }
        region = resolve_region_from_az(az)
        token_params = {
            "cluster_name": cluster_name, "cluster_owner": cluster_owner,
            "cluster_owner_email": cluster_owner_email, "az": az,
            "headnode_instance_type": headnode_instance_type,
            "overrides": dict(sorted((overrides or {}).items())),
            "defaults": _defaults_fingerprint(cluster_name),
        }
        return {
            "cluster_name": cluster_name,
            "region": region,
            "defaults_file": (
                os.path.basename(_defaults_path)
                if _defaults_path else None
            ),
            "resolved_config": resolved,
            "non_default_settings": {
                k: v for k, v in sorted((overrides or {}).items())
            },
            "defaults_file_settings": _from_file,
            # Only what is genuinely still a hardcoded default. Filtering on
            # overrides alone reported base_os as ubuntu2404 while the file
            # built ubuntu2404arm -- a preview stating the wrong OS to the
            # operator approving the build.
            "notable_defaults": {
                k: MAKE_CLUSTER_DEFAULTS[k]
                for k in ("ebs_encryption", "efs_encryption", "cluster_type",
                          "base_os", "scaledown_idletime")
                if k in MAKE_CLUSTER_DEFAULTS
                and k not in (overrides or {})
                and k not in _file_defaults
            },
            "confirmation_token": mint("create_cluster", token_params),
            "next_step": (
                "Call create_cluster with this confirmation_token and the same "
                "arguments. The token expires in 15 minutes. Building takes "
                "20-45 minutes and is not waited on."
            ),
        }

    @tool
    def create_cluster(
        cluster_name: str, cluster_owner: str, cluster_owner_email: str,
        az: str, headnode_instance_type: str, confirmation_token: str,
        overrides: dict | None = None,
    ) -> dict:
        """Build a cluster. Requires a token from preview_cluster_config.

        Kicked off without waiting: a build takes 20-45 minutes and a
        single MCP call cannot block for it. Poll list_clusters(live=True)
        or check_cluster_health for progress.
        """
        token_params = {
            "cluster_name": cluster_name, "cluster_owner": cluster_owner,
            "cluster_owner_email": cluster_owner_email, "az": az,
            "headnode_instance_type": headnode_instance_type,
            "overrides": dict(sorted((overrides or {}).items())),
            "defaults": _defaults_fingerprint(cluster_name),
        }
        # Token first, before the denylist and the build, for the same
        # reason delete_cluster verifies first: the gate must not be
        # reachable-around.
        verify(confirmation_token, "create_cluster", token_params)
        _reject_denied(overrides)
        params = build_make_cluster_params(
            cluster_name=cluster_name, cluster_owner=cluster_owner,
            cluster_owner_email=cluster_owner_email, az=az,
            headnode_instance_type=headnode_instance_type,
            overrides=overrides,
        )
        region = resolve_region_from_az(az)
        # core_create_cluster returns a CreateClusterResult, but its shared
        # validation helpers (p_fail/refer_to_docs_and_quit/illegal_az_msg)
        # still sys.exit(1) with the message already printed. SystemExit is
        # a BaseException, and this tool cannot use _cluster_lock's
        # translation because the core locks internally -- so without this
        # net one of those paths takes the whole server down instead of
        # failing one call. Deliberately narrow: it catches only SystemExit.
        try:
            return _plain(core_create_cluster(
                params=params, repo_root=_repo_root(), region=region,
                cluster_build_command=f"mcp create_cluster {cluster_name}",
                ansible_version="", wait=False,
            ))
        except SystemExit as e:
            raise PClusterMakerError(
                f"cluster creation for {cluster_name!r} failed during validation "
                f"(exit {e.code}); the reason was printed to the server log."
            )

    @tool
    def preview_cluster_delete(cluster_name: str,
                               delete_s3_bucketname: bool = True) -> dict:
        """Show what deleting a cluster would destroy, and mint the
        confirmation token delete_cluster requires.

        Read-only: it performs no AWS mutation. The token binds this exact
        cluster and these exact options, so a delete that differs from what
        was previewed is refused rather than silently proceeding.
        """
        rec = _require_record(cluster_name)
        params = {
            "cluster_name": cluster_name,
            "delete_s3_bucketname": delete_s3_bucketname,
        }
        return {
            "cluster_name": cluster_name,
            "region": rec.region,
            "cluster_owner": rec.cluster_owner,
            "serial": rec.serial,
            "will_delete": [
                f"CloudFormation stack {cluster_name} (head node, compute fleet, networking)",
                f"IAM role and managed policies for serial {rec.serial}",
                f"EC2 keypair {rec.ec2_keypair} and its local .pem",
                f"Secrets Manager secret for {cluster_name}",
            ] + (
                [f"S3 bucket {rec.s3_bucketname}"] if delete_s3_bucketname
                else [f"S3 bucket {rec.s3_bucketname} will be RETAINED"]
            ),
            "will_retain": [
                f"CloudWatch log groups /aws/parallelcluster/{cluster_name}-* "
                f"(the only record of a failed build; expire after 180 days)",
            ],
            "confirmation_token": mint("delete_cluster", params),
            # Both halves are minted here rather than from a second preview
            # tool: they describe one previewed operation, and a teardown
            # outlives a 15-minute token, so the operator re-previews before
            # finalizing anyway and gets a fresh pair when they do.
            "finalization_token": mint("finalize_cluster_teardown", params),
            "next_step": (
                "Call delete_cluster with confirmation_token and the same "
                "arguments; once the stack is gone, call "
                "finalize_cluster_teardown with finalization_token to remove "
                "what it left behind. Tokens expire in 15 minutes -- preview "
                "again for a fresh pair."
            ),
        }

    @tool
    def delete_cluster(cluster_name: str, confirmation_token: str,
                       delete_s3_bucketname: bool = True) -> dict:
        """Tear down a cluster. Requires a token from preview_cluster_delete.

        The token is verified against these exact arguments, so what runs is
        what was previewed. It is not authentication -- see
        mcp_server/confirmation_token.py -- it is a guard against acting
        without having shown the operator what would be destroyed.

        Kicked off without waiting: teardown takes 15-20 minutes and a single
        MCP call cannot block for it. Nothing local is cleaned up on this
        path; poll list_clusters and re-run once the stack is gone.
        """
        # Token first, before the record lookup. Both orders "work", but
        # this one keeps the gate unreachable-around: a caller cannot use
        # a missing-cluster error to probe which names exist without
        # holding a valid token, and a bad token fails as a bad token
        # rather than as whatever the record lookup happens to say first.
        params = {
            "cluster_name": cluster_name,
            "delete_s3_bucketname": delete_s3_bucketname,
        }
        verify(confirmation_token, "delete_cluster", params)
        rec = _require_record(cluster_name)
        return _plain(core_delete_cluster(
            cluster_name=cluster_name, cluster_owner=rec.cluster_owner,
            region=rec.region, repo_root=_repo_root(),
            delete_s3_bucketname="true" if delete_s3_bucketname else "false",
            debug_mode=False, wait=False,
        ))

    @tool
    def finalize_cluster_build(cluster_name: str) -> dict:
        """Finish a build that create_cluster started. LOCAL TRANSPORT ONLY.

        create_cluster returns as soon as CloudFormation accepts the stack,
        because no tool may block for a 20-45 minute build. Every step
        after that needs a head node that answers, so none of it runs: the
        access scripts are rendered to a temp directory and discarded, the
        staging tree never reaches the head node, and the build summary is
        never sent. Re-running the build cannot finish them -- it refuses
        on the vars file it wrote itself.

        Takes no confirmation token: it destroys nothing and only completes
        work the operator already authorized by building. Refuses unless
        the stack is CREATE_COMPLETE, and never waits for it.
        """
        rec = _require_record(cluster_name)
        return _plain(core_finalize_cluster_build(
            cluster_name=cluster_name, cluster_owner=rec.cluster_owner,
            region=rec.region, repo_root=_repo_root(),
        ))

    @tool
    def finalize_cluster_teardown(cluster_name: str, confirmation_token: str,
                                  delete_s3_bucketname: bool = True) -> dict:
        """Finish a teardown delete_cluster started. Requires a token from
        preview_cluster_delete (its finalization_token).

        delete_cluster returns as soon as CloudFormation accepts the delete,
        because no tool may block on a 15-20 minute operation. That leaves
        the IAM policies, the S3 bucket, the EC2 keypair and its .pem, the
        Secrets Manager secret, the SNS topic and the shared-store record
        all still present. This removes them.

        Calling delete_cluster a second time also completes the teardown,
        but only by re-issuing a stack delete against the name first --
        which deletes the new stack if that name has been rebuilt since,
        and which silently reports success again if the stack is still
        deleting. This tool issues no delete and says which it saw.

        Refuses unless the stack is confirmed gone, and never waits for it:
        poll list_clusters until the cluster disappears, then call this.
        """
        params = {
            "cluster_name": cluster_name,
            "delete_s3_bucketname": delete_s3_bucketname,
        }
        verify(confirmation_token, "finalize_cluster_teardown", params)
        rec = _require_record(cluster_name)
        return _plain(core_delete_cluster(
            cluster_name=cluster_name, cluster_owner=rec.cluster_owner,
            region=rec.region, repo_root=_repo_root(),
            delete_s3_bucketname="true" if delete_s3_bucketname else "false",
            debug_mode=False, finalize_only=True,
        ))

    @tool
    def rotate_cluster_key(cluster_name: str, dry_run: bool = False) -> dict:
        """Rotate a cluster's SSH keypair. LOCAL TRANSPORT ONLY.

        Returns status and paths only -- never key material. Excluded from
        the remote transport because the .pem it writes has to land on the
        operator's own filesystem; see this module's docstring.
        """
        rec = _require_record(cluster_name)
        result = core_rotate_cluster_key(
            cluster_record=rec, region=rec.region, dry_run=dry_run,
        )
        return _plain(result)

    @tool
    def manage_grafana_tunnel(cluster_name: str, port: int = 8443,
                              stop: bool = False) -> dict:
        """Open or close the Grafana SSH tunnel. LOCAL TRANSPORT ONLY.

        An SSH local port forward is only meaningful on the caller's own
        machine; see this module's docstring.
        """
        rec = _require_record(cluster_name)
        script = os.path.join(
            _repo_root(), "active_clusters", cluster_name,
            f"grafana_tunnel.{cluster_name}.sh",
        )
        result = core_manage_grafana_tunnel(
            cluster_record=rec, tunnel_script_path=script, port=port, stop=stop,
        )
        return _plain(result)

    return mcp
