# Forge

Run `arcus on forge` to install the host tools used by Forge and Forge
Dashboard. The module declares the shared tools even when another module
already provides them. Arcus adopts an existing `bubblewrap` installation
and marks it explicitly installed.

The Arch packages `bubblewrap`, `github-cli`, and `rustup` provide `bwrap`,
`gh`, and the `cargo` toolchain proxy. See Forge's [host requirements][e2]
for per-harness and per-gate requirements. Harness CLIs are managed
separately, and `pip-audit` belongs in each project's uv dev dependencies.

Semgrep is built from its [official PyPI release][semgrep] with a private
Python runtime. The AUR recipe depends on system Python packages and pulls
documentation from a moving branch. The local recipe pins the interpreter,
license, and every wheel, including transitive dependencies.

To update Semgrep, set `pkgver`, refresh the license checksum against the
release tag, and regenerate the lock from `modules/forge`. Use a temporary
input file `requirements.in` containing `semgrep==<version>`.

```sh
uv --no-config pip compile --python-version 3.14.10 \
    --python-platform x86_64-manylinux_2_34 --only-binary :all: \
    --format pylock.toml --no-header --no-annotate \
    -o pkg/semgrep/pylock.toml requirements.in
```

Update the lockfile checksum in `PKGBUILD` and verify the Semgrep wheel hash
against PyPI. For Python updates, use the interpreter version in that command
and verify the archive against the [upstream SHA256SUMS][python].

[e2]: https://github.com/poipoi300/forge/issues/13
[semgrep]: https://pypi.org/project/semgrep/
[python]: https://github.com/astral-sh/python-build-standalone/releases
