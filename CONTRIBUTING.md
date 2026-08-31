# Contributing to ParallelClusterMaker

Thanks for your interest.  This document covers what you need to know before
opening an issue or a pull request.

## Before you start

For anything larger than a bug fix, open an issue first and describe what you
want to change.  This toolkit provisions billable AWS infrastructure and
carries a lot of hard-won constraints that are not obvious from the code —
a short conversation up front is cheaper than a rejected pull request.

Bugs and features: https://github.com/rmarable/ParallelClusterMaker/issues

## Licensing of contributions

This project is **source-available, not open source**: Apache License 2.0
with the Commons Clause restriction, which withholds the right to sell.  See
`LICENSE` for the full terms, including the Licensor's clarification
permitting fees for professional services.

By submitting a contribution you agree that:

- Your contribution is licensed to the project under the same terms as
  `LICENSE` — inbound matches outbound.
- You grant Rodney Marable a perpetual, worldwide, non-exclusive,
  royalty-free, irrevocable license to use, reproduce, modify, distribute,
  sublicense, and relicense your contribution, **including under terms that
  permit sale**.

The second point exists for a specific reason.  The Commons Clause reserves
the right to sell to the Licensor, who may grant it to others.  Without a
relicensing grant from contributors, that permission could not be granted for
the project as a whole — a single unlicensed patch would make part of the
codebase impossible to include, permanently.  The grant costs you nothing you
would otherwise retain: you keep the copyright in your work and may do
anything you like with it elsewhere.

If your employer owns your work, make sure you have permission to contribute
before you do.  If you are contributing on behalf of a company and need a
signed agreement rather than this clause, open an issue and say so.

## Sign your commits

Every commit must carry a `Signed-off-by` trailer certifying the Developer
Certificate of Origin.  Commit with `-s`:

```console
$ git commit -s -m "your message"
```

CI rejects a pull request with any unsigned commit.  See `DCO.md` for the
certificate text and how to sign off work you have already written.

## AI-assisted contributions

They are welcome, under a published policy: disclose the tool or model,
review every line you submit, and do not point an autonomous agent at the
issue tracker.  Read `AI_POLICY.md` before submitting — it is short, and
contributions that ignore it may be closed.

Note that `Co-Authored-By` (crediting a tool) and `Signed-off-by`
(certifying provenance) are different trailers doing different jobs.  A
commit written with AI assistance carries both; only a human can sign off.

## Development setup

**Python 3.12 only.**  `aws-parallelcluster` does not support 3.13 or 3.14.

```console
$ python3.12 -m venv .venv
$ .venv/bin/pip install -r requirements.txt
```

Always use the project venv — `.venv/bin/python`, `.venv/bin/pytest` — never
the system Python.  Every top-level script fires a venv guard at import time
and will refuse to run outside it.

**Node.js (>= 10.13.0) must be on your `PATH`.**  ParallelCluster shells out
to the AWS CDK to synthesize CloudFormation, on your machine, and fails
immediately without it.  See `INSTALL.md`.

## The three gates

All three must pass before a pull request is reviewed.  CI runs them on
every push:

```console
$ make test
$ make lint
$ make shellcheck
```

`make lint` runs a pyflakes undefined-name sweep, `ruff check`, and
`ruff format --check`.  It never rewrites your files — run `ruff format .`
yourself if the format check fails.

## How this codebase tests

Worth knowing before you write a test here, because the conventions are
stricter than most projects:

- **No test may reach AWS.**  This is enforced at botocore's HTTP layer in
  `tests/conftest.py`.  An unstubbed call passes wherever there are
  credentials and fails in CI, far from its cause.
- **Fakes are written from the API contract, never from memory of it.**  For
  AWS that means reading `botocore/data/<service>/*/service-2.json` in your
  venv.  A fake built from recall agrees with the code by construction,
  including where the code is wrong — that has hidden real defects here more
  than once.
- **A value stated on more than one surface needs a guard that they agree.**
  Copies drift.  If you cannot generate the value from one source, pin the
  copies with a test.
- **A guard needs a vacuity guard.**  A test that would pass even with the
  behavior removed is not protecting anything.  Show that it fails when the
  thing it checks is broken.
- **Run the suite after any change to Python, Jinja2 templates, or
  `conftest.py`.**

`CLAUDE.md` documents the architectural constraints in detail.  It is written
for AI coding assistants but is the best available map of what will break if
you change it — read the sections touching whatever you are modifying.

## Style

- American English throughout — docs, comments, help text, error strings.
  The exception is an external contract: Slurm's `CANCELLED` job state is
  passed verbatim to `sacct` and must not be Americanized.
- No comments unless the *why* is non-obvious.
- No docstrings beyond a single short line.
- No backwards-compatibility shims.
- Prefer editing an existing file over creating a new one.
- No emojis.

## Submitting

Push your branch and open a pull request against `main`.  Fill in the
template — it is short, and every line on it is something a reviewer would
otherwise have to ask you.

Describe what changed and why.  If you fixed a bug, say how you know it is
fixed; if the answer is a test, name it.
