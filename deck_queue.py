from pydantic import BaseModel
from vercel.queue import Topic


class DeckJobMessage(BaseModel):
    job_id: str


deck_jobs = Topic[DeckJobMessage]("deck-generation")
