# reference/ — local-only content

This directory holds material that must **never be committed** to this repository.

## OpenConstructionERP source (AGPL-3.0)

- `OpenConstructionERP-main/` — the exported zip of
  https://github.com/datadrivenconstruction/OpenConstructionERP.git, extracted
  and kept **unmodified** as an audit reference.
- License: **AGPL-3.0-or-later** (with a commercial-license alternative from
  DataDrivenConstruction).

Because the license is AGPL-3.0, re-publishing this source through our
repository would make our repository a distribution of AGPL material and
complicate our own licensing position. The reference therefore stays
**local-only** (see `.gitignore`). `docs/license-analysis.md` records what we
may reuse, adapt, or must rewrite/avoid.

To refresh the reference:

```bash
cd reference
wget https://github.com/datadrivenconstruction/OpenConstructionERP/archive/refs/heads/main.zip
unzip main.zip   # creates OpenConstructionERP-main/
```

## chat-plan.md

Private product-planning conversation snapshot (local-only).

## Status of the audit

`docs/reuse-matrix.md` and `docs/license-analysis.md` are the authoritative
record of what was inspected and what may be extracted.
