# Developer Certificate of Origin

Every commit in this project must be signed off under the Developer
Certificate of Origin, reproduced verbatim below.  Sign off by committing
with `-s`:

```console
$ git commit -s -m "your message"
```

That appends a trailer naming you as the author:

```text
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match the commit author.  `git commit -s` does this
for you from your git configuration.  To sign off a branch you already wrote,
rebase over it:

```console
$ git rebase --signoff origin/main
```

CI checks every commit in a pull request and fails if any is missing the
trailer.

## A note on wording

The certificate below is a fixed document — its own terms say changing it is
not allowed — and it refers throughout to "the open source license indicated
in the file."  **This project is not open source.**  It is source-available:
Apache License 2.0 with the Commons Clause restriction, which withholds the
right to sell.

Read that phrase as "the license indicated in the file," meaning the license
in `LICENSE`.  Signing off certifies that you have the right to submit your
contribution under *that* license.  See `CONTRIBUTING.md` for what
contributing licenses to this project.

---

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.
1 Letterman Drive
Suite D4700
San Francisco, CA, 94129

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
