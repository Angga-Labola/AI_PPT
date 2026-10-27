"""Define the typed message that Vercel Queues passes to the worker."""

from pydantic import BaseModel
from vercel.queue import Topic


class DeckJobMessage(BaseModel):
    """A queue message only needs the ID; the full job data lives in Neon."""

    job_id: str


# A topic is like a named inbox: the API publishes here and the worker listens.
deck_jobs = Topic[DeckJobMessage]("deck-generation")
