# AI Policy

AI-assisted contributions are welcome. We ask that you:

- Disclose that AI was used and name the tool/model.
- Review and understand every line you submit; you are responsible for it.
- Meet the same quality, testing, and style standards as any contribution —
  `make test`, `make lint`, and `make shellcheck` must pass, and code must
  follow the style rules in `CLAUDE.md` (no comments unless the *why* is
  non-obvious, no docstrings beyond a single line, no backwards-compatibility
  shims).
- Not use fully autonomous agents to open issues or PRs.
- Respond to reviewers yourself.
- Clearly mark AI-generated text in descriptions and issues.

This applies to issues and comments as well as pull requests. Using AI for
translation or grammar help is fine. Contributions that ignore this policy may
be closed.

## Crediting AI Assistance

AI tools may be named as commit co-authors, following the convention already
used throughout this repository's history:

```text
Co-Authored-By: <Tool/Model> <noreply@vendor.example>
```

For example:

```text
Co-Authored-By: Claude Code <noreply@anthropic.com>
```

The human author is fully responsible for the commit regardless of the
trailer — crediting a tool documents *how* the work was done, it does not
transfer responsibility for it. The standard this project holds AI-assisted
work to is the same one `CLAUDE.md`'s Behavior section holds every session
to: don't fabricate, don't guess silently, ask before assuming. A contribution
that fails that standard is held to it whether a human or an AI tool produced
the line in question.
