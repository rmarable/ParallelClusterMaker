#!/usr/bin/env bash
# Integration smoke test for ParallelClusterMaker.
#
# Provisions a cluster using a caller-supplied defaults file, runs a short
# Slurm job, then tears it down.
#
# MUST BE INVOKED MANUALLY — not run by pytest or CI.
#
# Usage:
#   ./tests/integration/run_integration_test.sh \
#       --az us-east-1a \
#       --owner myusername \
#       --email me@example.com \
#       --defaults /path/to/my-cluster_defaults.yml \
#       [--profile my-aws-profile] \
#       [--keep]
#
# Options:
#   --az        AWS Availability Zone (required, e.g. us-east-1a)
#   --owner     cluster_owner (required, lowercase letters/digits/hyphens)
#   --email     cluster_owner_email (required)
#   --defaults  Path to a pcluster defaults YAML file (required)
#   --profile   AWS CLI profile name (optional, uses $AWS_PROFILE if set)
#   --keep      Skip teardown on success (leaves cluster running for inspection)
#
# Prerequisites:
#   - Active .venv with aws-parallelcluster installed (source .venv/bin/activate)
#   - AWS credentials configured with sufficient IAM permissions
#   - pcluster CLI on PATH (installed via pip in .venv)
#   - jq installed (brew install jq / apt install jq)
#
# Estimated cost: varies by instance type and region.
# Estimated duration: 25-40 minutes.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

AZ=""
OWNER=""
EMAIL=""
DEFAULTS_FILE=""
AWS_PROFILE="${AWS_PROFILE:-}"
KEEP=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
START_TIME="$(date +%s)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --az)       AZ="$2";           shift 2 ;;
        --owner)    OWNER="$2";        shift 2 ;;
        --email)    EMAIL="$2";        shift 2 ;;
        --defaults) DEFAULTS_FILE="$2"; shift 2 ;;
        --profile)  AWS_PROFILE="$2";  shift 2 ;;
        --keep)     KEEP=true;         shift ;;
        *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$AZ" || -z "$OWNER" || -z "$EMAIL" || -z "$DEFAULTS_FILE" ]]; then
    echo "ERROR: --az, --owner, --email, and --defaults are all required." >&2
    echo "Usage: $0 --az us-east-1a --owner test --email test@example.com --defaults /path/to/defaults.yml" >&2
    exit 1
fi

if [[ ! -f "$DEFAULTS_FILE" ]]; then
    echo "ERROR: defaults file not found: ${DEFAULTS_FILE}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------

REGION="${AZ%?}"
DATESTAMP="$(date +%H%M%S)"
CLUSTER_NAME="itest-${DATESTAMP}"
PASS=false
CLUSTER_CREATED=false

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "[$(date '+%H:%M:%S')] FAIL: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Cleanup trap — always runs on exit
# ---------------------------------------------------------------------------

cleanup() {
    local exit_code=$?
    if [[ "$CLUSTER_CREATED" == "true" ]]; then
        if [[ "$PASS" == "false" || "$KEEP" == "false" ]]; then
            log "Tearing down cluster ${CLUSTER_NAME} ..."
            cd "$REPO_ROOT"
            set +e
            python kill_pcluster.py \
                -N "$CLUSTER_NAME" \
                -O "$OWNER" \
                -A "$AZ" \
                2>&1 | tee -a /tmp/itest-kill-${CLUSTER_NAME}.log
            set -e
            log "Teardown initiated. Check CloudFormation console if issues persist."
        else
            log "--keep is set and test passed; cluster ${CLUSTER_NAME} left running."
            log "To tear it down: python kill_pcluster.py -N ${CLUSTER_NAME} -O ${OWNER} -A ${AZ}"
        fi
    fi
    if [[ "$PASS" == "true" ]]; then
        log "PASSED in ${ELAPSED_MINS}m ${ELAPSED_SECS}s"
    else
        local _e=$(( $(date +%s) - START_TIME ))
        log "FAILED after $(( _e / 60 ))m $(( _e % 60 ))s (exit code ${exit_code})"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Step 1: Pre-flight checks
# ---------------------------------------------------------------------------

log "=== Integration smoke test: ${CLUSTER_NAME} ==="
log "AZ=${AZ}  owner=${OWNER}  email=${EMAIL}  defaults=${DEFAULTS_FILE}  keep=${KEEP}"

if [[ -n "$AWS_PROFILE" ]]; then
    export AWS_PROFILE
    log "Using AWS profile: ${AWS_PROFILE}"
fi

log "Checking AWS credentials..."
aws sts get-caller-identity > /dev/null \
    || fail "aws sts get-caller-identity failed — check credentials."

log "Checking pcluster is on PATH..."
command -v pcluster > /dev/null \
    || fail "pcluster not found — activate the .venv first: source .venv/bin/activate"

log "Checking jq is available..."
command -v jq > /dev/null \
    || fail "jq not found — install it: brew install jq  or  apt install jq"

log "Checking venv is active..."
python -c "
import os, sys
venv = os.path.join('$REPO_ROOT', '.venv')
if not os.path.realpath(sys.prefix).startswith(os.path.realpath(venv)):
    sys.exit(1)
" || fail "Active venv is not the repo .venv — run: source .venv/bin/activate"

# ---------------------------------------------------------------------------
# Step 2: Create cluster
# ---------------------------------------------------------------------------

log "Creating cluster ${CLUSTER_NAME} ..."
cd "$REPO_ROOT"
python make_pcluster.py \
    -N "$CLUSTER_NAME" \
    -O "$OWNER" \
    -E "$EMAIL" \
    -A "$AZ" \
    --use_defaults="$DEFAULTS_FILE" \
    2>&1 | tee /tmp/itest-create-${CLUSTER_NAME}.log
CLUSTER_CREATED=true

# ---------------------------------------------------------------------------
# Step 3: Assert CREATE_COMPLETE
# ---------------------------------------------------------------------------

log "Polling for CREATE_COMPLETE (max 30 min)..."
POLL_LIMIT=60  # 60 x 30s = 30 min
for i in $(seq 1 $POLL_LIMIT); do
    STATUS="$(pcluster describe-cluster --cluster-name "$CLUSTER_NAME" --region "$REGION" \
        | jq -r '.clusterStatus // "UNKNOWN"' 2>/dev/null || echo "UNKNOWN")"
    log "  [${i}/${POLL_LIMIT}] clusterStatus=${STATUS}"
    if [[ "$STATUS" == "CREATE_COMPLETE" ]]; then
        log "Cluster reached CREATE_COMPLETE."
        break
    fi
    if [[ "$STATUS" == "CREATE_FAILED" || "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
        fail "Cluster reached ${STATUS} — check CloudFormation events."
    fi
    if [[ $i -eq $POLL_LIMIT ]]; then
        fail "Timed out waiting for CREATE_COMPLETE after 30 min."
    fi
    sleep 30
done

# ---------------------------------------------------------------------------
# Step 4: Get head node IP and SSH key
# ---------------------------------------------------------------------------

log "Fetching head node IP..."
HEAD_IP="$(pcluster describe-cluster --cluster-name "$CLUSTER_NAME" --region "$REGION" \
    | jq -r '.headNode.publicIpAddress // .headNode.privateIpAddress // empty')"
[[ -n "$HEAD_IP" ]] || fail "Could not determine head node IP."
log "Head node IP: ${HEAD_IP}"

SSH_KEY="$(find "${REPO_ROOT}/active_clusters/${CLUSTER_NAME}" -name '*.pem' 2>/dev/null | head -1)"
[[ -f "$SSH_KEY" ]] || fail "SSH private key not found under active_clusters/${CLUSTER_NAME}/"

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o BatchMode=yes -i ${SSH_KEY}"

EC2_USER="ubuntu"
if grep -q "^ec2_user:" "$DEFAULTS_FILE" 2>/dev/null; then
    EC2_USER="$(grep "^ec2_user:" "$DEFAULTS_FILE" | awk '{print $2}')"
fi

# ---------------------------------------------------------------------------
# Step 5: SSH smoke test
# ---------------------------------------------------------------------------

log "Waiting for SSH to be ready (max 5 min)..."
for i in $(seq 1 10); do
    if ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" "echo SSH_OK" 2>/dev/null | grep -q SSH_OK; then
        log "SSH is ready."
        break
    fi
    if [[ $i -eq 10 ]]; then
        fail "SSH not ready after 5 min."
    fi
    sleep 30
done

log "Running SSH smoke test (hostname + sinfo)..."
HOSTNAME_OUT="$(ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" "hostname")"
log "  hostname: ${HOSTNAME_OUT}"
echo "$HOSTNAME_OUT" | grep -qi "$CLUSTER_NAME" \
    || fail "hostname does not contain cluster name '${CLUSTER_NAME}'."

SINFO_OUT="$(ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" "sinfo --noheader")"
log "  sinfo: ${SINFO_OUT}"
[[ -n "$SINFO_OUT" ]] || fail "sinfo returned no output — Slurm may not be running."

# ---------------------------------------------------------------------------
# Step 6: Slurm job smoke test
# ---------------------------------------------------------------------------

log "Submitting Slurm smoke job..."
JOB_ID="$(ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" \
    "sbatch --wrap 'sleep 5 && echo INTEGRATION_TEST_OK' --output /tmp/itest-job.out -p queue0 \
    | awk '{print \$NF}'")"
[[ "$JOB_ID" =~ ^[0-9]+$ ]] || fail "sbatch did not return a numeric job ID (got: '${JOB_ID}')."
log "Submitted job ID: ${JOB_ID}"

log "Polling for job completion (max 5 min)..."
for i in $(seq 1 10); do
    STATE="$(ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" \
        "sacct -j ${JOB_ID} --noheader --format=State --parsable2 2>/dev/null | head -1" || echo "PENDING")"
    log "  [${i}/10] job ${JOB_ID} state=${STATE}"
    if echo "$STATE" | grep -q "COMPLETED"; then
        log "Job completed."
        break
    fi
    if echo "$STATE" | grep -qE "FAILED|CANCELLED|TIMEOUT"; then
        fail "Slurm job ${JOB_ID} ended in state ${STATE}."
    fi
    if [[ $i -eq 10 ]]; then
        fail "Timed out waiting for Slurm job ${JOB_ID} to complete."
    fi
    sleep 30
done

log "Checking job output for INTEGRATION_TEST_OK..."
JOB_OUT="$(ssh $SSH_OPTS "${EC2_USER}@${HEAD_IP}" "cat /tmp/itest-job.out 2>/dev/null || echo ''")"
echo "$JOB_OUT" | grep -q "INTEGRATION_TEST_OK" \
    || fail "Expected 'INTEGRATION_TEST_OK' in job output but got: '${JOB_OUT}'"
log "Job output verified."

# ---------------------------------------------------------------------------
# Done — print summary
# ---------------------------------------------------------------------------

PASS=true
ELAPSED_MINS=$(( ( $(date +%s) - START_TIME ) / 60 ))
ELAPSED_SECS=$(( ( $(date +%s) - START_TIME ) % 60 ))

echo ""
echo "=================================================================="
echo "             Integration Smoke Test — PASSED"
echo "=================================================================="
echo "  Cluster Name:    ${CLUSTER_NAME}"
echo "  Owner:           ${OWNER}"
echo "  Region / AZ:     ${REGION} / ${AZ}"
echo "  Defaults file:   ${DEFAULTS_FILE}"
echo "  Head Node IP:    ${HEAD_IP}"
echo "  Slurm Job ID:    ${JOB_ID}  (COMPLETED)"
echo "  Elapsed:         ${ELAPSED_MINS}m ${ELAPSED_SECS}s"
echo ""
if [[ "$KEEP" == "true" ]]; then
echo "  Cluster is still running (--keep was set)."
echo "  To tear it down:"
echo "    ./kill_pcluster.py -N ${CLUSTER_NAME} -O ${OWNER} -A ${AZ}"
else
echo "  Teardown will begin now."
fi
echo "=================================================================="
echo ""
