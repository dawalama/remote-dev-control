import pytest
from remote_dev_ctrl.server.session_manager import (
    SessionStatus,
    AttentionState,
    get_attention_state,
    get_session_manager,
)
from remote_dev_ctrl.server.db.models import Collection
from remote_dev_ctrl.server.db.repositories import get_collection_repo


def test_attention_state_mapping():
    """Verify revised spec attention states:
    - Needs Attention: waiting, verifying, failed
    - Running: pending, running
    - Staged: proposed
    - Accepted: completed
    - Closed: cancelled, done
    """
    assert get_attention_state(SessionStatus.WAITING) == AttentionState.NEEDS_ATTENTION
    assert get_attention_state(SessionStatus.VERIFYING) == AttentionState.NEEDS_ATTENTION
    assert get_attention_state(SessionStatus.FAILED) == AttentionState.NEEDS_ATTENTION

    assert get_attention_state(SessionStatus.PENDING) == AttentionState.RUNNING
    assert get_attention_state(SessionStatus.RUNNING) == AttentionState.RUNNING

    assert get_attention_state(SessionStatus.PROPOSED) == AttentionState.STAGED

    assert get_attention_state(SessionStatus.COMPLETED) == AttentionState.ACCEPTED

    assert get_attention_state(SessionStatus.CANCELLED) == AttentionState.CLOSED
    assert get_attention_state(SessionStatus.DONE) == AttentionState.CLOSED


def test_proposed_session_start_idempotency():
    """Verify proposed session can be claimed for start, and two clicks create one run."""
    sm = get_session_manager()
    session = sm.create_session(
        channel_id="ch-test-proposed",
        project="demo-project",
        description="A proposed goal waiting for 1-tap start",
        status=SessionStatus.PROPOSED,
    )
    assert session.status == SessionStatus.PROPOSED
    assert get_attention_state(session.status) == AttentionState.STAGED

    # First start click claims it
    claim1 = sm.claim_for_start(session.id)
    assert claim1 == "proposed"

    fresh = sm.get(session.id)
    assert fresh.status == SessionStatus.RUNNING
    assert get_attention_state(fresh.status) == AttentionState.RUNNING

    # Second click in parallel is rejected (returns None)
    claim2 = sm.claim_for_start(session.id)
    assert claim2 is None


def test_collection_roles():
    """Verify collection roles: unsorted, own, client.
    - Default is unsorted
    - General stays unsorted
    - At most one collection can be own
    """
    repo = get_collection_repo()
    
    # General collection
    gen = repo.get("general")
    assert gen is not None
    assert gen.role == "unsorted"

    # Attempt to change general to own or client is prevented
    gen.role = "own"
    updated_gen = repo.update(gen)
    assert updated_gen.role == "unsorted"

    # Create new client collection
    c1 = repo.create(Collection(name="Client Acme", role="client"))
    assert c1.role == "client"

    # Create new own collection
    c2 = repo.create(Collection(name="My Company", role="own"))
    assert c2.role == "own"

    # Create another own collection -> previous own reverts to unsorted
    c3 = repo.create(Collection(name="Holding Co", role="own"))
    assert c3.role == "own"
    
    fresh_c2 = repo.get(c2.id)
    assert fresh_c2.role == "unsorted"

    # Cleanup test collections
    repo.delete(c1.id)
    repo.delete(c2.id)
    repo.delete(c3.id)


def test_session_task_id_and_parent_chain():
    """Verify session can link to task_id and parent_session_id for multi-run goal cards."""
    from remote_dev_ctrl.server.supervised_sessions import create_supervised_session
    from remote_dev_ctrl.server.db.models import Project
    from remote_dev_ctrl.server.db.repositories import get_project_repo

    project_repo = get_project_repo()
    test_proj = project_repo.create(Project(name="test-multi-run-proj", path="/tmp/test-proj"))

    try:
        # Run 1: Initial attempt on task-101
        sess1 = create_supervised_session(
            channel_id="ch-test-chain",
            project="test-multi-run-proj",
            goal="Add OAuth token refresh",
            status="proposed",
            task_id="task-101",
        )
        assert sess1.task_id == "task-101"
        assert sess1.status == SessionStatus.PROPOSED

        # Run 2: Follow-up attempt on task-101 linking to parent sess1
        sess2 = create_supervised_session(
            channel_id="ch-test-chain",
            project="test-multi-run-proj",
            goal="Add OAuth token refresh",
            status="pending",
            task_id="task-101",
            metadata_extra={"parent_session_id": sess1.id},
        )
        assert sess2.task_id == "task-101"
        assert sess2.metadata.get("parent_session_id") == sess1.id
        assert sess2.status == SessionStatus.PENDING
    finally:
        project_repo.delete(test_proj.id)
