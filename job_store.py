"""Store background-job state and lasting deck-download details in Neon."""

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config import DATABASE_URL


def _connect() -> psycopg.Connection[dict[str, Any]]:
    """Open one database connection; the context manager closes it after use."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)


def initialize_job_store() -> None:
    """Create new tables and safely add columns/indexes to existing deployments."""
    with _connect() as connection:
        # Keep a compact lookup separate from temporary job data so old job rows
        # can be cleaned up without breaking the library or emailed download links.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deck_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                filename TEXT,
                blob_pathname TEXT,
                metadata JSONB,
                gemini_api_calls INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "ALTER TABLE deck_jobs ADD COLUMN IF NOT EXISTS "
            "gemini_api_calls INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deck_downloads (
                job_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                blob_pathname TEXT NOT NULL,
                metadata JSONB NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO deck_downloads
                (job_id, filename, blob_pathname, metadata, completed_at)
            SELECT id, filename, blob_pathname,
                jsonb_build_object('title', COALESCE(metadata->>'title', filename)),
                updated_at
            FROM deck_jobs
            WHERE status = 'completed'
            ON CONFLICT (job_id) DO NOTHING
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS deck_downloads_completed_at_idx "
            "ON deck_downloads (completed_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS deck_jobs_created_at_idx "
            "ON deck_jobs (created_at DESC)"
        )


def create_job(job_id: str, payload: dict[str, Any]) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO deck_jobs (id, status, payload) VALUES (%s, 'queued', %s)",
            (job_id, Jsonb(payload)),
        )


def fail_queued_job(job_id: str, error: str) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE deck_jobs SET status = 'failed', error = %s, payload = '{}'::jsonb,
                updated_at = now()
            WHERE id = %s AND status = 'queued'
            """,
            (error[:1000], job_id),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        return connection.execute(
            "SELECT * FROM deck_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()


def list_completed_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as connection:
        return list(
            connection.execute(
                """
                SELECT job_id AS id, 'completed'::text AS status,
                    completed_at AS created_at, completed_at AS updated_at,
                    filename, blob_pathname, metadata
                FROM deck_downloads
                ORDER BY completed_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        )


def get_completed_job_by_filename(filename: str) -> dict[str, Any] | None:
    with _connect() as connection:
        return connection.execute(
            """
            SELECT job_id AS id, filename, blob_pathname
            FROM deck_downloads
            WHERE filename = %s
            """,
            (filename,),
        ).fetchone()


def claim_job(job_id: str) -> dict[str, Any] | None:
    """Atomically mark a queued job running so duplicate deliveries cannot race."""
    with _connect() as connection:
        connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'failed', error = 'Worker stopped before completing the job.',
                payload = '{}'::jsonb, updated_at = now()
            WHERE id = %s AND status = 'running' AND attempts >= 2
              AND updated_at < now() - interval '45 seconds'
            """,
            (job_id,),
        )
        return connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'running', attempts = attempts + 1, updated_at = now(), error = NULL
            WHERE id = %s
              AND attempts < 2
              AND (
                    status = 'queued'
                    OR (status = 'running' AND updated_at < now() - interval '45 seconds')
                  )
            RETURNING *
            """,
            (job_id,),
        ).fetchone()


def mark_job_cancelled(job_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'cancelled', payload = '{}'::jsonb, updated_at = now()
            WHERE id = %s AND status IN ('running', 'cancel_requested')
            """,
            (job_id,),
        )


def heartbeat_or_cancel(job_id: str) -> bool:
    """Refresh a running job's timestamp, or report that cancellation was requested."""
    with _connect() as connection:
        row = connection.execute(
            """
            UPDATE deck_jobs
            SET updated_at = now()
            WHERE id = %s AND status = 'running'
            RETURNING id
            """,
            (job_id,),
        ).fetchone()
        if row:
            return False
        connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'cancelled', payload = '{}'::jsonb, updated_at = now()
            WHERE id = %s AND status = 'cancel_requested'
            """,
            (job_id,),
        )
        return True


def finish_job(
    job_id: str,
    filename: str,
    blob_pathname: str,
    metadata: dict[str, Any],
) -> bool:
    """Mark the job complete and save its small, long-lived download lookup."""
    with _connect() as connection:
        row = connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'completed', filename = %s, blob_pathname = %s,
                metadata = %s, payload = '{}'::jsonb, updated_at = now()
            WHERE id = %s AND status = 'running'
            RETURNING id, updated_at
            """,
            (filename, blob_pathname, Jsonb(metadata), job_id),
        ).fetchone()
        if row is None:
            return False
        connection.execute(
            """
            INSERT INTO deck_downloads
                (job_id, filename, blob_pathname, metadata, completed_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE SET
                filename = EXCLUDED.filename,
                blob_pathname = EXCLUDED.blob_pathname,
                metadata = EXCLUDED.metadata,
                completed_at = EXCLUDED.completed_at
            """,
            (
                job_id,
                filename,
                blob_pathname,
                Jsonb({"title": metadata.get("title", filename)}),
                row["updated_at"],
            ),
        )
        return True


def purge_terminal_jobs(retention_days: int = 7) -> int:
    """Remove only old completed, failed, or cancelled job rows—not deck indexes."""
    if retention_days < 1:
        raise ValueError("Terminal-job retention must be at least one day.")
    with _connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM deck_jobs
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND updated_at < now() - (%s * interval '1 day')
            """,
            (retention_days,),
        )
        return cursor.rowcount


def add_model_api_calls(job_id: str, count: int) -> None:
    if count <= 0:
        return
    with _connect() as connection:
        connection.execute(
            """
            UPDATE deck_jobs
            SET gemini_api_calls = gemini_api_calls + %s, updated_at = now()
            WHERE id = %s AND status = 'running'
            """,
            (count, job_id),
        )


def get_model_api_calls(job_id: str) -> int:
    with _connect() as connection:
        row = connection.execute(
            "SELECT gemini_api_calls FROM deck_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Generation job {job_id} was not found.")
        return row["gemini_api_calls"]


def record_job_error(job_id: str, error: str) -> bool:
    with _connect() as connection:
        row = connection.execute(
            """
            UPDATE deck_jobs
            SET status = CASE WHEN attempts >= 2 THEN 'failed' ELSE 'queued' END,
                error = %s,
                payload = CASE WHEN attempts >= 2 THEN '{}'::jsonb ELSE payload END,
                updated_at = now()
            WHERE id = %s AND status = 'running'
            RETURNING status
            """,
            (error[:1000], job_id),
        ).fetchone()
        return bool(row and row["status"] == "queued")


def request_job_cancellation(job_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        return connection.execute(
            """
            UPDATE deck_jobs
            SET status = CASE
                    WHEN status = 'queued' THEN 'cancelled'
                    WHEN status = 'running' THEN 'cancel_requested'
                    ELSE status
                END,
                payload = CASE WHEN status = 'queued' THEN '{}'::jsonb ELSE payload END,
                updated_at = now()
            WHERE id = %s
            RETURNING id, status, updated_at
            """,
            (job_id,),
        ).fetchone()


def delete_completed_job(filename: str) -> dict[str, Any] | None:
    """Remove a deck's lookup and job row; the caller separately deletes its Blob."""
    with _connect() as connection:
        row = connection.execute(
            """
            DELETE FROM deck_downloads
            WHERE filename = %s
            RETURNING job_id AS id, blob_pathname
            """,
            (filename,),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "DELETE FROM deck_jobs WHERE id = %s AND status = 'completed'",
            (row["id"],),
        )
        return row
