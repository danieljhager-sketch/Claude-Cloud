import pytest

from job_agent.sources import Job
from job_agent.store import Store


def job(source_id, title="Backend Engineer"):
    return Job("lever", source_id, "Acme", title, "Remote", "https://x", "https://x/apply", "desc", remote=True)


def test_jobs_are_saved_once():
    store = Store(":memory:")
    assert store.add_job(job("1")) is True
    assert store.add_job(job("1")) is False
    assert store.get("lever:1")["title"] == "Backend Engineer"
    assert store.counts() == {"new": 1}


def test_scoring_and_best_unapplied():
    store = Store(":memory:")
    for i, score in enumerate([55, 91, 78, 85]):
        store.add_job(job(str(i)))
        store.set_score(f"lever:{i}", score, {"score": score})
    store.set_status("lever:3", "applied")
    best = store.best_unapplied(min_score=70, limit=5)
    assert [row["score"] for row in best] == [91, 78]


def test_unknown_status_is_rejected():
    store = Store(":memory:")
    store.add_job(job("1"))
    with pytest.raises(ValueError):
        store.set_status("lever:1", "hired")


def test_boards():
    store = Store(":memory:")
    assert store.add_board("ashby", "gizmo", "web search") is True
    assert store.add_board("ashby", "gizmo", "web search") is False
    assert store.boards() == [("ashby", "gizmo")]
