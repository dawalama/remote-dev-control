import uuid
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from remote_dev_ctrl.cli import app as cli_app
from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.channel_manager import (
    ChannelManager,
    assert_link_allowed,
    get_channel_manager,
)
from remote_dev_ctrl.server.db.models import Channel, ChannelType, Collection, Project
from remote_dev_ctrl.server.db.repositories import (
    get_collection_repo,
    get_project_repo,
)
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager
from remote_dev_ctrl.server.supervised_sessions import create_supervised_session


def _auth_client() -> TestClient:
    token, _ = get_auth_manager().create_token("test-coll-links", Role.ADMIN)
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_collection(repo, name: str, role: str = "unsorted", cid: str | None = None) -> Collection:
    c = Collection(id=cid or _unique("coll"), name=name, role=role)
    return repo.create(c)


def _create_project(repo, name: str, collection_id: str = "general") -> Project:
    p = Project(name=name, path=f"/tmp/{name}", description="test", collection_id=collection_id)
    return repo.create(p)


def test_assert_link_allowed():
    cm = get_channel_manager()
    c_sys = Channel(id="ch-system", name="#system", type=ChannelType.SYSTEM, collection_id="general")
    p_gen = Project(name="p1", path="/tmp/p1", collection_id="general")
    p_sug = Project(name="p2", path="/tmp/p2", collection_id="sugarwork")

    with pytest.raises(ValueError, match="system_channel_has_no_projects"):
        assert_link_allowed(c_sys, p_gen)

    c_gen = Channel(id="ch-gen", name="#gen", type=ChannelType.PROJECT, collection_id="general")
    assert_link_allowed(c_gen, p_gen)

    with pytest.raises(ValueError, match="project_collection_mismatch"):
        assert_link_allowed(c_gen, p_sug)


def test_create_channel_collection_mismatch_422():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()

    coll = _create_collection(c_repo, _unique("client-coll"), role="client")
    proj = _create_project(p_repo, _unique("p-general"), collection_id="general")

    resp = client.post(
        "/channels",
        json={
            "name": _unique("ch"),
            "collection_id": coll.id,
            "project_ids": [proj.id],
        },
    )
    assert resp.status_code == 422
    assert "project_collection_mismatch" in resp.text


def test_add_project_to_channel_collection_mismatch_422():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    coll = _create_collection(c_repo, _unique("client-coll"), role="client")
    ch = cm.create_channel(name=_unique("#ch"), collection_id=coll.id)
    proj = _create_project(p_repo, _unique("p-general"), collection_id="general")

    resp = client.post(
        f"/channels/{ch.id}/projects",
        json={"project_name": proj.name},
    )
    assert resp.status_code == 422
    assert "project_collection_mismatch" in resp.text


def test_update_channel_collection_blocked_while_linked_422():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    coll1 = _create_collection(c_repo, _unique("coll-1"), role="client")
    coll2 = _create_collection(c_repo, _unique("coll-2"), role="client")
    proj = _create_project(p_repo, _unique("p-coll1"), collection_id=coll1.id)

    ch = cm.create_channel(name=_unique("#ch"), collection_id=coll1.id, project_ids=[proj.id])

    # Attempting to change collection_id while project is linked returns 422 move_projects_instead
    resp = client.patch(
        f"/channels/{ch.id}",
        json={"collection_id": coll2.id},
    )
    assert resp.status_code == 422
    assert "move_projects_instead" in resp.text

    # Empty ephemeral channel can change collection
    empty_ch = cm.create_channel(name=_unique("#empty"), collection_id=coll1.id)
    resp2 = client.patch(
        f"/channels/{empty_ch.id}",
        json={"collection_id": coll2.id},
    )
    assert resp2.status_code == 200
    assert cm.get_channel(empty_ch.id).collection_id == coll2.id


def test_move_sole_project_channel_follows():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest"), role="client")
    proj = _create_project(p_repo, _unique("proj-sole"), collection_id="general")
    ch = cm.create_channel(name=_unique("#sole-ch"), collection_id="general", project_ids=[proj.id])

    resp = client.post(
        f"/projects/{proj.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["collection_id"] == dest.id
    assert ch.id in data["channels_moved"]
    assert not data["links_dropped"]

    # Verify channel row now points to dest
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == dest.id


def test_move_mixed_channel_drops_link_and_repairs_to_remaining_projects():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-sugar"), role="client")
    p1 = _create_project(p_repo, _unique("p1-move"), collection_id="general")
    p2 = _create_project(p_repo, _unique("p2-stay"), collection_id="general")

    # Mixed channel with p1 and p2 in general
    ch = cm.create_channel(name=_unique("#mixed"), collection_id="general", project_ids=[p1.id, p2.id])

    # Old bug scenario: channel was already pointing at dest (e.g. sugarwork)
    cm.db.execute("UPDATE channels SET collection_id = ? WHERE id = ?", (dest.id, ch.id))
    cm.db.commit()

    resp = client.post(
        f"/projects/{p1.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert ch.id in data["links_dropped"]
    assert ch.id not in data["channels_moved"]

    # Channel should now point to general (the remaining project's collection)
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == "general"

    # Project 1 link is removed, Project 2 link is preserved
    linked_pids = [
        r["project_id"]
        for r in cm.db.execute("SELECT project_id FROM channel_projects WHERE channel_id = ?", (ch.id,)).fetchall()
    ]
    assert p1.id not in linked_pids
    assert p2.id in linked_pids


def test_workstream_settings_move_keeps_every_linked_project():
    """Workstream Settings moves each linked project, then links them again.

    A direct channel collection change is rejected while projects are linked.
    Moving the projects one at a time drops every link but the last. Linking
    them again leaves this workstream intact at the destination. Another
    workstream that still has a project in the old collection drops the one
    that moved.
    """
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-together"), role="client")
    p1 = _create_project(p_repo, _unique("p1-together"), collection_id="general")
    p2 = _create_project(p_repo, _unique("p2-together"), collection_id="general")
    stay = _create_project(p_repo, _unique("p-stay"), collection_id="general")

    ch = cm.create_channel(name=_unique("#together"), collection_id="general", project_ids=[p1.id, p2.id])
    other = cm.create_channel(name=_unique("#other"), collection_id="general", project_ids=[p1.id, stay.id])

    for name in (p1.name, p2.name):
        resp = client.post(f"/projects/{name}/move", json={"collection_id": dest.id})
        assert resp.status_code == 200, resp.text

    for name in (p1.name, p2.name):
        resp = client.post(f"/channels/{ch.id}/projects", json={"project_name": name})
        assert resp.status_code == 200, resp.text

    assert p_repo.get(p1.name).collection_id == dest.id
    assert p_repo.get(p2.name).collection_id == dest.id
    assert p_repo.get(stay.name).collection_id == "general"
    assert cm.get_channel(ch.id).collection_id == dest.id
    assert cm.get_channel(other.id).collection_id == "general"

    linked = {
        r["project_id"]
        for r in cm.db.execute(
            "SELECT project_id FROM channel_projects WHERE channel_id = ?",
            (ch.id,),
        ).fetchall()
    }
    assert linked == {p1.id, p2.id}

    other_linked = {
        r["project_id"]
        for r in cm.db.execute(
            "SELECT project_id FROM channel_projects WHERE channel_id = ?",
            (other.id,),
        ).fetchall()
    }
    assert other_linked == {stay.id}


def test_move_retry_no_early_return():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-retry"), role="client")
    p1 = _create_project(p_repo, _unique("p1-retry"), collection_id=dest.id)  # already at destination
    p2 = _create_project(p_repo, _unique("p2-retry"), collection_id="general")

    # Channel links both but says dest.id
    ch = cm.create_channel(name=_unique("#retry-ch"), collection_id=dest.id)
    cm.db.execute("INSERT OR IGNORE INTO channel_projects (channel_id, project_id) VALUES (?, ?)", (ch.id, p1.id))
    cm.db.execute("INSERT OR IGNORE INTO channel_projects (channel_id, project_id) VALUES (?, ?)", (ch.id, p2.id))
    cm.db.commit()

    # Even though p1.collection_id == dest.id, the move must NOT return early; it must repair
    resp = client.post(
        f"/projects/{p1.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert ch.id in data["links_dropped"]

    # Repaired to p2's collection (general)
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == "general"


def test_move_session_only_channel_repair():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    sm = get_session_manager()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-sess"), role="client")
    p1 = _create_project(p_repo, _unique("p1-sess"), collection_id="general")
    p2 = _create_project(p_repo, _unique("p2-sess"), collection_id="general")

    # Channel only links p2, but old bug had set collection_id to dest
    ch = cm.create_channel(name=_unique("#sess-ch"), collection_id="general", project_ids=[p2.id])
    cm.db.execute("UPDATE channels SET collection_id = ? WHERE id = ?", (dest.id, ch.id))
    cm.db.commit()

    # A historical session for p1 references this channel
    sm.create_session(
        channel_id=ch.id,
        project=p1.name,
        description="Session on channel",
        status=SessionStatus.COMPLETED,
    )

    # Move p1 to dest
    resp = client.post(
        f"/projects/{p1.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200

    # The channel is repaired to general (where remaining project p2 is)
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == "general"


def test_move_session_only_channel_no_links_not_retargeted():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    sm = get_session_manager()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-nolink"), role="client")
    p1 = _create_project(p_repo, _unique("p1-nolink"), collection_id="general")

    # Channel has no linked projects at all and points to dest
    ch = cm.create_channel(name=_unique("#empty-ch"), collection_id=dest.id)

    sm.create_session(
        channel_id=ch.id,
        project=p1.name,
        description="Session on unlinked channel",
        status=SessionStatus.COMPLETED,
    )

    # Move p1: empty channel is left alone (not retargeted)
    resp = client.post(
        f"/projects/{p1.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == dest.id


def test_delete_channel_project_link_does_not_repair():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    coll = _create_collection(c_repo, _unique("coll-del"), role="client")
    p1 = _create_project(p_repo, _unique("p1-del"), collection_id=coll.id)
    p2 = _create_project(p_repo, _unique("p2-del"), collection_id=coll.id)

    ch = cm.create_channel(name=_unique("#del-link"), collection_id=coll.id, project_ids=[p1.id, p2.id])

    # Deleting link for p1 should ONLY delete channel_projects row
    resp = client.delete(f"/channels/{ch.id}/projects/{p1.name}")
    assert resp.status_code == 200

    linked = [
        r["project_id"]
        for r in cm.db.execute("SELECT project_id FROM channel_projects WHERE channel_id = ?", (ch.id,)).fetchall()
    ]
    assert p1.id not in linked
    assert p2.id in linked
    # Collection remains unchanged
    assert cm.get_channel(ch.id).collection_id == coll.id


def test_system_channel_remains_and_drops_link_on_move():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    dest = _create_collection(c_repo, _unique("dest-sys"), role="client")
    proj = _create_project(p_repo, _unique("p-sys"), collection_id="general")

    # Manually insert link to system channel to test migration/cleanup
    cm.ensure_system_channel()
    cm.db.execute("INSERT OR IGNORE INTO channel_projects (channel_id, project_id) VALUES ('ch-system', ?)", (proj.id,))
    cm.db.commit()

    resp = client.post(
        f"/projects/{proj.name}/move",
        json={"collection_id": dest.id},
    )
    assert resp.status_code == 200

    # Link dropped
    link = cm.db.execute(
        "SELECT 1 FROM channel_projects WHERE channel_id = 'ch-system' AND project_id = ?", (proj.id,)
    ).fetchone()
    assert link is None

    # #system channel is unchanged
    sys_ch = cm.get_channel("ch-system")
    assert sys_ch is not None
    assert sys_ch.id == "ch-system"

    # #system still allows session for project in another collection
    sess = create_supervised_session(
        channel_id="ch-system",
        project=proj.name,
        goal="Session on system workstream",
    )
    assert sess is not None


def test_delete_collection_moves_projects_and_retargets_channels():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    # General cannot be deleted
    resp_gen = client.delete("/collections/general")
    assert resp_gen.status_code == 400
    assert resp_gen.json()["detail"] == "Cannot delete the Unclassified collection"

    coll = _create_collection(c_repo, _unique("to-delete"), role="client")
    proj = _create_project(p_repo, _unique("p-in-del"), collection_id=coll.id)
    ch = cm.create_channel(name=_unique("#ch-del"), collection_id=coll.id, project_ids=[proj.id])

    # Delete the collection
    resp = client.delete(f"/collections/{coll.id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Project moved to general
    fresh_p = p_repo.get(proj.name)
    assert fresh_p.collection_id == "general"

    # Channel retargeted to general
    fresh_ch = cm.get_channel(ch.id)
    assert fresh_ch.collection_id == "general"


def test_archive_collection_hides_without_moving_projects():
    client = _auth_client()
    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    cm = get_channel_manager()

    refused = client.post("/collections/general/archive")
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Cannot archive the Unclassified collection"

    coll = _create_collection(c_repo, _unique("to-archive"), role="client")
    proj = _create_project(p_repo, _unique("p-arch"), collection_id=coll.id)
    ch = cm.create_channel(name=_unique("#ch-arch"), collection_id=coll.id, project_ids=[proj.id])

    archived = client.post(f"/collections/{coll.id}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"]

    assert p_repo.get(proj.name).collection_id == coll.id
    assert cm.get_channel(ch.id).collection_id == coll.id
    assert c_repo.get(coll.id) is not None

    visible_ids = {row["id"] for row in client.get("/collections").json()}
    assert coll.id not in visible_ids
    assert "general" in visible_ids

    hidden = next(row for row in client.get("/collections?include_archived=true").json() if row["id"] == coll.id)
    assert hidden["archived_at"]

    restored = client.post(f"/collections/{coll.id}/unarchive")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert coll.id in {row["id"] for row in client.get("/collections").json()}


def test_cli_collections_bootstrap():
    runner = CliRunner()
    result = runner.invoke(cli_app, ["collections", "bootstrap"])
    assert result.exit_code == 0
    assert "truesteps" in result.stdout.lower() or "exists" in result.stdout.lower()

    # Verify rows exist in DB
    c_repo = get_collection_repo()
    for cid in ("truesteps", "sugarwork", "quillmeetings", "personal"):
        coll = c_repo.get(cid)
        assert coll is not None

    # Run again: idempotent
    result2 = runner.invoke(cli_app, ["collections", "bootstrap"])
    assert result2.exit_code == 0
    assert "exists" in result2.stdout.lower()
