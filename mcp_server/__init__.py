"""MCP server for ParallelClusterMaker (Workstream 5).

Deliberately importable without AWS credentials and without the venv guard
every top-level CLI script fires at import time: the tool wrappers here are
thin adapters over src/pcluster_core.py's core_* functions, and the whole
point of Workstream 1's core/shim split was that those are callable from
something other than a CLI. Importing this package must therefore stay
side-effect-free -- no boto3 clients, no credential resolution, no
sys.exit() -- so the in-memory FastMCP Client tests can drive it with
nothing stubbed but the AWS layer itself.
"""
