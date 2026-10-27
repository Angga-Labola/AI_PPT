import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import job_store


class FakeResult:
    def __init__(self, row=None, rowcount=0) -> None:
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, results=None) -> None:
        self.results = list(results or [])
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=()):
        self.statements.append((query, params))
        return self.results.pop(0) if self.results else FakeResult()


class JobStoreRetentionTests(unittest.TestCase):
    def test_finish_job_writes_download_index_in_same_transaction(self) -> None:
        completed_at = datetime.now(timezone.utc)
        connection = FakeConnection(
            [FakeResult({"id": "job-123", "updated_at": completed_at}), FakeResult()]
        )
        with patch.object(job_store, "_connect", return_value=connection):
            completed = job_store.finish_job(
                "job-123",
                "deck.pptx",
                "decks/job-123/deck.pptx",
                {"title": "Deck"},
            )

        self.assertTrue(completed)
        self.assertIn("UPDATE deck_jobs", connection.statements[0][0])
        self.assertIn("INSERT INTO deck_downloads", connection.statements[1][0])
        self.assertEqual(connection.statements[1][1][0:3], (
            "job-123",
            "deck.pptx",
            "decks/job-123/deck.pptx",
        ))

    def test_purge_only_deletes_terminal_records_older_than_retention(self) -> None:
        connection = FakeConnection([FakeResult(rowcount=3)])
        with patch.object(job_store, "_connect", return_value=connection):
            deleted = job_store.purge_terminal_jobs(7)

        query, parameters = connection.statements[0]
        self.assertEqual(deleted, 3)
        self.assertIn("status IN ('completed', 'failed', 'cancelled')", query)
        self.assertIn("updated_at < now() - (%s * interval '1 day')", query)
        self.assertEqual(parameters, (7,))

    def test_purge_rejects_nonpositive_retention(self) -> None:
        with self.assertRaises(ValueError):
            job_store.purge_terminal_jobs(0)

    def test_delete_completed_job_removes_index_and_optionally_old_job_row(self) -> None:
        connection = FakeConnection(
            [FakeResult({"id": "job-123", "blob_pathname": "decks/job-123/deck.pptx"})]
        )
        with patch.object(job_store, "_connect", return_value=connection):
            row = job_store.delete_completed_job("deck.pptx")

        self.assertEqual(row["id"], "job-123")
        self.assertIn("DELETE FROM deck_downloads", connection.statements[0][0])
        self.assertIn("DELETE FROM deck_jobs", connection.statements[1][0])


if __name__ == "__main__":
    unittest.main()
