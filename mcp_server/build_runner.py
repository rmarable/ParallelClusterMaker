"""One asynchronous cluster build: the half that touches AWS.

`mcp_server/build.py` holds the marker and payload and touches nothing.
This runs the build and makes sure its outcome is discoverable, because
the invocation that starts it is an `Event` and has no caller waiting on a
return value.

**Silence here is indistinguishable from success**, the same trap the
teardown completion runner exists to avoid. `core_create_cluster` already
records every failure after the first AWS mutation -- that is what
`get_build_status` reads. What it cannot record is a failure *before* the
lock is taken, because the store is addressed from an account ID the
build has not fetched yet. Synchronously that did not matter: those paths
return in seconds and the caller saw them. Asynchronously nobody is there,
so this records what the core could not.
"""

import json


def _log(payload, outcome, extra=""):
    """One structured line per build. The function's log group is retained,
    so this is the record that survives the invocation."""
    params = payload.get("params") or {}
    print(json.dumps({
        "pcm_build": True,
        "cluster": params.get("cluster_name"),
        "region": payload.get("region"),
        "outcome": outcome,
        "extra": extra,
    }, default=str), flush=True)


def _record_failure_if_unrecorded(payload, message):
    """Publish a build failure the core could not.

    Reads first, because a post-lock failure has already written a record
    naming the stage it failed in -- more specific than anything knowable
    here, and overwriting it with "build" would discard the useful half.
    Best-effort throughout: this runs after a build has already failed, and
    a bookkeeping error must not be what the operator ends up debugging.
    """
    params = payload.get("params") or {}
    cluster_name = params.get("cluster_name")
    region = payload.get("region")
    if not cluster_name or not region:
        return False
    try:
        from mcp_server.tools import _record_store
        from pcluster_core import _publish_build_failure, get_build_failure

        s3, bucket = _record_store(region)
        if s3 is None:
            return False
        if get_build_failure(
            s3, locks_bucketname=bucket, cluster_name=cluster_name
        ):
            return False
        return _publish_build_failure(
            s3, locks_bucketname=bucket, cluster_name=cluster_name,
            region=region, cluster_owner=params.get("cluster_owner", ""),
            stage="validation", message=message,
        )
    except Exception as e:  # noqa: BLE001 - see docstring
        print(f"pcm_build: could not record the failure: "
              f"{type(e).__name__}: {e}", flush=True)
        return False


def run_build(payload, *, create=None):
    """Run one build to its kicked-off point. Returns a plain dict.

    `create` is an injectable seam so a test drives the real control flow
    -- which failure is recorded, and which is left to the core -- without
    reaching AWS.
    """
    params = payload.get("params") or {}
    region = payload.get("region")

    if create is None:
        from pcluster_core import (
            MakeClusterParams, core_create_cluster, resolve_writable_repo_root,
        )

        def create(p, r):
            return core_create_cluster(
                params=MakeClusterParams(**p), region=r,
                repo_root=payload.get("repo_root") or resolve_writable_repo_root(),
                cluster_build_command=(
                    f"mcp create_cluster {p.get('cluster_name', '')}"),
                ansible_version="", wait=False,
            )

    try:
        result = create(params, region)
    except BaseException as e:  # noqa: BLE001
        # BaseException, not Exception: the shared validation helpers still
        # sys.exit(), and SystemExit is a BaseException. Synchronously that
        # reached the caller; here it would vanish.
        from pcluster_core import pcluster_exception_detail

        detail = (pcluster_exception_detail(e) if isinstance(e, Exception)
                  else "") or str(e) or type(e).__name__
        message = f"{type(e).__name__}: {detail}".strip().rstrip(":")
        _log(payload, "exception", message)
        _record_failure_if_unrecorded(payload, message)
        return {"started": True, "success": False, "message": message}

    success = bool(getattr(result, "success", False))
    message = getattr(result, "message", "") or ""
    _log(payload, "success" if success else "failed", message)
    if not success:
        _record_failure_if_unrecorded(payload, message)
    return {"started": True, "success": success, "message": message}
