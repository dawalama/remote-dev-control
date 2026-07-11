"""Run a no-write supervised-session smoke test against a local RDC server."""

import time

import httpx

from remote_dev_ctrl.server.auth import Role, get_auth_manager


def main() -> None:
    manager = get_auth_manager()
    token, info = manager.create_token("live-session-smoke", Role.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = "http://127.0.0.1:8420"
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
            projects = client.get("/projects").raise_for_status().json()
            channels = client.get("/channels").raise_for_status().json()
            project = next(
                (item["name"] for item in projects if item["name"] == "remote-dev-control"),
                projects[0]["name"],
            )
            channel = next(
                (item for item in channels if project in item.get("project_names", [])),
                next(item for item in channels if item["type"] != "system"),
            )
            created = client.post(
                "/sessions",
                json={
                    "channel_id": channel["id"],
                    "project": project,
                    "goal": "Exercise the migrated supervised-session lifecycle without project writes",
                    "acceptance_criteria": ["Diagnostic execution and independent verification succeed"],
                    "provider": "diagnostic",
                },
            ).raise_for_status().json()
            session_id = created["id"]
            client.post(f"/sessions/{session_id}/retry").raise_for_status()

            session = created
            for _ in range(15):
                time.sleep(1)
                session = client.get(f"/sessions/{session_id}").raise_for_status().json()
                if session["status"] in {"verifying", "failed"}:
                    break
            if session["status"] != "verifying":
                raise RuntimeError(f"Diagnostic execution ended in {session['status']}")

            git_evidence = client.post(
                f"/sessions/{session_id}/evidence/git"
            ).raise_for_status().json()
            verification = client.post(
                f"/sessions/{session_id}/evidence/verification",
                json={"command": "git status --short"},
            ).raise_for_status().json()
            evidence_ids = [git_evidence["id"], verification["id"]]
            client.put(
                f"/sessions/{session_id}/criteria/0",
                json={"status": "passed", "evidence_ids": evidence_ids},
            ).raise_for_status()
            client.post(f"/sessions/{session_id}/complete").raise_for_status()
            completed = client.get(f"/sessions/{session_id}").raise_for_status().json()
            print(f"session={session_id}")
            print(f"project={project}")
            print(f"status={completed['status']}")
            print(f"evidence={len(evidence_ids)}")
    finally:
        manager.revoke_token(info.id)


if __name__ == "__main__":
    main()
