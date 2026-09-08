# License Analysis — boq-v2 vs OpenConstructionERP

**Status:** v1.0 · Round 1 · Synthesizes the licensing audit.
> **This is engineering input, not legal advice.** AGPL boundary questions
> (combined works, data compilations, §13 scope) are genuinely contested.
> Before revenue-bearing deployment, a short review by a software-licensing
> lawyer is cheap insurance.

## 1. The facts

| Item | Finding | Source (reference copy) |
|---|---|---|
| OCErp license | **AGPL-3.0-or-later** | `LICENSE`, `COPYRIGHT` |
| Copyright holder | Artem Boiko / DataDrivenConstruction.io (German entity) | `COPYRIGHT` |
| Commercial license | Template only, not binding; would cover closed-source deployment of OCErp *code* — but explicitly **excludes PyMuPDF** (§4a) and does not obviously cover the data catalogues | `COMMERCIAL-LICENSE.md` |
| CLA | Contributions dual-licensed to the owner including "any proprietary license of the Project Owner's choosing" | `CLA.md` §2 |
| Patents | Unilateral non-assertion covenant scoped to use of the Software *as distributed* — does not clearly bless reimplementations | `PATENTS.md` §2 |
| PyMuPDF | AGPL / Artifex commercial; present in every install; actively enforced by Artifex | `NOTICE` "AGPL Cascade" |

## 2. AGPL mechanics for our use case

### 2a. Copy OCErp code into our closed SaaS → **fatal, never**
AGPL §13 (Remote Network Interaction): running a modified version that users
interact with over a network requires offering **every user the Corresponding
Source of our version** — our own backend included (§5 defines Corresponding
Source to include the whole combined work minus arm's-length separate works).
No distribution threshold to hide behind; §13 exists precisely to close the
SaaS loophole. **This is why our architecture adopts patterns, never code.**

### 2b. Run unmodified OCErp as a separate HTTP service → (we don't, but documented)
Unmodified, arm's-length, socket-separated services do not make the caller a
derivative work. **Any** patch flips it into §13 territory for all our users.
Intimate coupling (shared process, imported as a library) collapses the
boundary. Not our strategy; documented to keep the boundary explicit.

### 2c. Copy OCErp data → **the most dangerous shortcut in this whole plan**
- **No rate data ships in `packs/`** — every pack is structure/tax/validation
  metadata JSONs, authored content under AGPL. `india-cpwd` explicitly
  reproduces no DSR rates ("the rates … are the publisher's, so none are
  reproduced here").
- Rate data lives in `data/catalog/` (7k global resource rows + 27 regional
  market CSVs incl. Mumbai/INR). `data/catalog/README.md` **explicitly claims
  AGPL-3.0 over "the compilation as published here"** (selection and
  arrangement), separate from the underlying public sources. Whether AGPL
  (written for "Programs") can bind a data compilation is uncertain — and DDC
  may additionally hold EU Database Directive *sui generis* rights over the
  900k+ row compilation. **Do not bet our license position on winning that
  argument.**
- Per-base source terms (as recorded by OCErp): Italy Toscana **CC BY 4.0
  (attribution required)**; Brazil SINAPI open data; Spain BCCA, China
  official tariff, Türkiye CSB, Greece public. **The Global CWICR base —
  from which all 11 market catalogues incl. Mumbai derive — is marked
  PENDING with no written basis.**
- **Defensible route: bypass DDC's compilation entirely.** Collect from
  original public sources (CPWD DSR for India, SINAPI for Brazil, Toscana
  with CC BY 4.0 attribution, …) and build our own selection. Never brand our
  data "CWICR" (DDC trademark).

### 2d. Read OCErp to learn, then write our own → **our primary strategy**
Copyright protects expression, not ideas or algorithms. Reimplementing an
algorithm in our own expression is permitted. The real risk for a solo
developer (same person reading and writing) is **unconscious copying of
expression**: names, docstrings, file layout, test fixtures, verbatim
thresholds. Hygiene rules (below) make this manageable; a two-room clean room
is overkill for our scale, same-author-with-discipline is proportionate.
Provenance comments ("reimplemented from public spec X; OCErp consulted for
behavioral understanding only") evidence independent creation. Patent risk
from `PATENTS.md` on reimplementations is speculative and unquantified — treat
as low; no evidence of relevant estimating patents.

### 2e. PyMuPDF → **banned in boq-v2**
AGPL / Artifex commercial, actively enforced. Options were open-source
everything, pay Artifex recurring, or avoid. **Decision: avoid.** Use
pdfplumber (MIT) + pypdf (BSD) + pypdfium2 (BSD/Apache) for extraction and
rendering. The one real capability cost: PyMuPDF provides pre-decoded drawing
paths; the permissive path needs a low-level walk over page objects composing
transform matrices and flattening curves — bounded, algorithmic work that we
own in `ingestion/pdf`.

### 2f. The local reference/ copy → **fine and keep it that way**
AGPL grants unconditional freedom to run/study/privately-modify a lawfully
obtained copy; obligations attach on *conveying*. The copy on disk distributes
nothing. The one real risk is accidental publication — handled by
`.gitignore` (reference/ excluded) plus a CI guard (fails if `reference/`
paths, `openconstructionerp`, or `app/modules/` appear in tracked files).

## 3. Strategy risk table

| Strategy | Obligations | Cost | Risk to closed-source goal | Verdict |
|---|---|---|---|---|
| Copy OCErp code | §13 full source disclosure to all users | 0 | **Fatal** | Never |
| Copy pack JSONs | AGPL on authored files | 0 | High (detectable) | Never; reimplement from public sources |
| Copy data/catalog CSVs | AGPL-claimed compilation + possible DB right; source terms beneath | 0 | High + legally uncertain (PENDING base) | Never wholesale; collect from original public sources |
| Pattern-inspire (read→reimplement) | None if expression is ours | dev time | Low, manageable | **Primary strategy** + hygiene rules |
| True clean-room (2 roles) | None | highest | lowest | Overkill at solo scale |
| Run unmodified OCErp side-by-side | none on our code until first patch | ops | Medium (fragile) | Not our path; documented |
| Buy OCErp commercial license | signed agreement; excludes PyMuPDF; data rights unclear | recurring fee | Low for covered code | Only if ever embedding OCErp; not needed |

## 4. Policy for this repository (binding rules)

1. **No OCErp source file, pack file, or catalog CSV is ever copied into
   boq-v2** — no copy-paste, no vendoring, no "temporary" snippets, including
   from `data/catalog/`, `packs/`, `backend/app/`.
2. **Reading rules:** treat `reference/` as a library, not a fork. Read at
   the design/behavior level; **write with the reference closed**; never
   mirror names, docstrings, file layout, fixtures, or verbatim numeric
   thresholds. Standards-based features are implemented from the public
   specification (IS 1200, DSR structure, MasterFormat), not from OCErp's
   files, with a one-line provenance comment.
3. **Data policy:** every imported dataset is checked against the *original
   publisher's* terms, never OCErp's packaging. V1 India catalogue: source
   DSR/CPWD schedules directly from CPWD publications (users may also
   bulk-import their own DSR/SoR — the product ships an importer, not a
   pirate ratebook). Toscana → CC BY 4.0 attribution if ever used. Mumbai/
   CWICR market CSVs → off-limits. Never use the mark "CWICR".
4. **Dependency allowlist:** MIT/Apache/BSD/PSF/Unlicense only by default.
   **PyMuPDF banned.** LGPL (psycopg2) only unmodified via pip — prefer
   asyncpg. opencv only headless and only if CV needed. Every new dependency
   gets an SPDX check before merging; copyleft/unclear → escalate, don't ship.
   (MPL-2.0 file-level packages like certifi/orjson are fine unmodified.)
5. **CI guard:** a check that fails the build if `reference/` paths or
   OCErp-identifying names (`openconstructionerp`, `oe_` module prefixes,
   `app/modules/`) appear in tracked files.
6. **No CLA signature, no upstream contributions** — the CLA hands the owner
   a commercial-licensing right over our contributed work.
7. **Trademarks:** OCErp/DDC/OpenEstimate/CWICR never appear in our product,
   marketing, or repo.
8. If our closed-source posture ever changes, revisit rules 1 and 4 — the
   calculus flips.

## 5. Permissive dependency baseline (verified against OCErp's own NOTICE + our needs)

**Green (use freely):** fastapi, pydantic v2, sqlalchemy 2, alembic, uvicorn,
asyncpg, ezdxf, pdfplumber, pypdf, pypdfium2, pillow, shapely (BSD — not in
OCErp's tree; verified independently), rapidfuzz, openpyxl, reportlab, httpx,
python-jose, bcrypt, structlog, uvicorn, defusedxml (PSF).

**Yellow (conditions):** psycopg2-binary (LGPL — unmodified only, prefer
asyncpg); opencv-python-headless (Apache declared; wheel carries FFmpeg
LGPL-2.1 — acceptable unmodified/dynamic, never freeze into one binary).

**Red (banned/never ship):** PyMuPDF; paddleocr's Qt-carrying opencv wheels;
GAEB schema/conformance files (copyrighted, not redistributable — if we ever
do GAEB, implement from the public Fachdokumentation).

## 6. Residual risks & mitigations

| Risk | Mitigation |
|---|---|
| Unconscious expression copying | Reading rules (§4.2), provenance comments, review discipline |
| Accidental commit of reference/ | .gitignore + CI guard (§4.5) |
| Data provenance challenged | Only original-source data with recorded terms; importer-first catalogue strategy |
| Artifex enforcement (if PyMuPDF sneaks in transitively) | Dependency audit in CI fails on any AGPL package |
| Patent assertion on reimplementation | Low/speculative; provenance comments; monitor |
| User-uploaded drawings' rights | ToS: users warrant they hold rights to uploaded drawings (Round B legal checklist) |
