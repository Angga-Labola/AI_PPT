from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config import DATABASE_URL


def _connect() -> psycopg.Connection[dict[str, Any]]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")
    return psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)


def initialize_job_store() -> None:
    with _connect() as connection:
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
                metadata JSONB
            )
            """
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
                SELECT id, status, created_at, updated_at, filename, blob_pathname, metadata
                FROM deck_jobs
                WHERE status = 'completed'
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        )


def get_completed_job_by_filename(filename: str) -> dict[str, Any] | None:
    with _connect() as connection:
        return connection.execute(
            """
            SELECT id, filename, blob_pathname
            FROM deck_jobs
            WHERE filename = %s AND status = 'completed'
            """,
            (filename,),
        ).fetchone()


def claim_job(job_id: str) -> dict[str, Any] | None:
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
    with _connect() as connection:
        row = connection.execute(
            """
            UPDATE deck_jobs
            SET status = 'completed', filename = %s, blob_pathname = %s,
                metadata = %s, payload = '{}'::jsonb, updated_at = now()
            WHERE id = %s AND status = 'running'
            RETURNING id
            """,
            (filename, blob_pathname, Jsonb(metadata), job_id),
        ).fetchone()
        return row is not None


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
    with _connect() as connection:
        return connection.execute(
            """
            DELETE FROM deck_jobs
            WHERE filename = %s AND status = 'completed'
            RETURNING id, blob_pathname
            """,
            (filename,),
        ).fetchone()
