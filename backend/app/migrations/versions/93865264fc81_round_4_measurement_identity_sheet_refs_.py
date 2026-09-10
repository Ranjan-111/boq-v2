"""round 4: measurement identity, sheet refs, parse warnings, export status

Round 4 API wiring (T019, T111-T113, T092, T103):
  * measurements.measurement_id + label - durable replay identity (uuid5 of
    inputs_digest) and display label persisted per row; unique per run so a
    duplicate identity is a refused insert, never silently kept.
  * drawing_sheets.sheet_ref - parser sheet identity ("modelspace",
    "paperspace:Layout1") used by calibration/engine/source handles; widens
    the sheet uniqueness to (drawing_file_id, page_number, sheet_ref).
  * drawing_files.parse_warnings - parse-time per-handle refusals persisted
    so measurement runs block on them (measure_parsed semantics).
  * export_artifacts.status - export jobs run async (pending to succeeded /
    failed); storage_key/sha256 are written only on success.

Down-rev: 864543ca2597 (baseline).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '93865264fc81'
down_revision = '864543ca2597'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'drawing_files',
        sa.Column('parse_warnings', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
    )
    op.add_column(
        'drawing_sheets',
        # The table has no writers before Round 4, but upgraded dev databases
        # may hold rows: add nullable, backfill, then enforce NOT NULL.
        sa.Column('sheet_ref', sa.String(length=80), nullable=True),
    )
    op.execute(
        "UPDATE drawing_sheets SET sheet_ref = page_number::text "
        "WHERE sheet_ref IS NULL"
    )
    op.alter_column('drawing_sheets', 'sheet_ref', nullable=False)
    op.drop_constraint('uq_drawing_sheets_drawing_file_id', 'drawing_sheets',
                       type_='unique')
    op.create_unique_constraint(
        'uq_drawing_sheets_drawing_file_id', 'drawing_sheets',
        ['drawing_file_id', 'page_number', 'sheet_ref'],
    )
    op.add_column(
        'export_artifacts',
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default='pending'),
    )
    op.add_column(
        'measurements',
        sa.Column('measurement_id', sa.String(length=64), nullable=False,
                  server_default=''),
    )
    op.add_column('measurements',
                  sa.Column('label', sa.String(length=512), nullable=True))
    op.create_index('ix_measurements_measurement_id', 'measurements',
                    ['measurement_id'], unique=False)
    # Per-run identity uniqueness. Existing rows (none in production — the
    # measurements table has no writers before Round 4) backfill empty + row
    # id so the constraint can be created on upgraded dev databases.
    op.execute(
        "UPDATE measurements SET measurement_id = id::text WHERE measurement_id = ''"
    )
    op.create_unique_constraint('uq_measurements_run_identity', 'measurements',
                                ['run_id', 'measurement_id'])


def downgrade() -> None:
    op.drop_constraint('uq_measurements_run_identity', 'measurements',
                       type_='unique')
    op.drop_index('ix_measurements_measurement_id', table_name='measurements')
    op.drop_column('measurements', 'label')
    op.drop_column('measurements', 'measurement_id')
    op.drop_column('export_artifacts', 'status')
    op.drop_constraint('uq_drawing_sheets_drawing_file_id', 'drawing_sheets',
                       type_='unique')
    op.create_unique_constraint('uq_drawing_sheets_drawing_file_id',
                                'drawing_sheets',
                                ['drawing_file_id', 'page_number'])
    op.drop_column('drawing_sheets', 'sheet_ref')
    op.drop_column('drawing_files', 'parse_warnings')
