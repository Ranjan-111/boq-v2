"""Backend services — the app-facing layer between routers and the domain.

Routers translate HTTP; services hold transactional use-cases (parse a
drawing, execute a run, build a BOQ) against persisted state. Services may
import the domain packages (ingestion, takeoff, core) and never the transport
in the other direction — routers call services, services never import routers.

Modules are imported explicitly (no re-exported star imports) so the
import-linter layering stays explicit per file.
"""
