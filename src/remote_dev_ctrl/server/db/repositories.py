"""Repository classes for database operations."""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid as uuid_mod
from datetime import datetime, timedelta
from typing import Optional

from .connection import get_db
from .models import (
    ActionKind,
    AgentSession,
    Collection,
    Project,
    Task,
    TaskStatus,
    TaskPriority,
    AgentRun,
    AgentRunStatus,
    Event,
    EventLevel,
    ProcessConfig,
    ProcessStatus,
    ProcessType,
    AgentState,
    AgentStatus,
    PortAssignment,
    VNCSession,
    VNCStatus,
    RecipeModel,
)


# =============================================================================
# PROJECT NAME ↔ UUID RESOLUTION
# =============================================================================

def _resolve_project_names(project_ids: list[str]) -> dict[str, str]:
    """Batch-resolve project UUIDs to names. Returns {id: name} mapping."""
    if not project_ids:
        return {}
    db = get_db("rdc")
    placeholders = ", ".join(["?"] * len(project_ids))
    cursor = db.execute(
        f"SELECT id, name FROM projects WHERE id IN ({placeholders})",
        project_ids,
    )
    return {row["id"]: row["name"] for row in cursor.fetchall()}


def _resolve_project_name(project_id: str) -> str:
    """Resolve a single project UUID to its name. Returns '' if not found."""
    if not project_id:
        return ""
    mapping = _resolve_project_names([project_id])
    return mapping.get(project_id, "")


def resolve_project_id(name_or_id: str) -> Optional[str]:
    """Resolve a project name or UUID to its UUID.

    Checks by id first, then by name. Returns None if not found.
    """
    if not name_or_id:
        return None
    db = get_db("rdc")
    cursor = db.execute(
        "SELECT id FROM projects WHERE id = ? OR name = ?",
        (name_or_id, name_or_id),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


class ProjectRepository:
    """Repository for project operations."""

    def __init__(self):
        self.db = get_db("rdc")

    def create(self, project: Project) -> Project:
        """Create a new project. Generates UUID if not set."""
        if not project.id:
            project.id = str(uuid_mod.uuid4())
        self.db.execute("""
            INSERT INTO projects (id, name, path, description, collection_id, tags, created_at, updated_at, config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project.id,
            project.name,
            project.path,
            project.description,
            project.collection_id or "general",
            json.dumps(project.tags) if project.tags else "[]",
            project.created_at.isoformat(),
            project.updated_at.isoformat(),
            json.dumps(project.config) if project.config else None,
        ))
        self.db.commit()
        return project

    def get(self, name_or_id: str) -> Optional[Project]:
        """Get a project by name or UUID."""
        cursor = self.db.execute(
            "SELECT * FROM projects WHERE name = ? OR id = ?",
            (name_or_id, name_or_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_project(row)

    def get_by_id(self, project_id: str) -> Optional[Project]:
        """Get a project by UUID."""
        cursor = self.db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        return self._row_to_project(row) if row else None

    def get_id_by_name(self, name: str) -> Optional[str]:
        """Resolve a project name to its UUID."""
        cursor = self.db.execute("SELECT id FROM projects WHERE name = ?", (name,))
        row = cursor.fetchone()
        return row["id"] if row else None

    def list(self) -> list[Project]:
        """List all projects."""
        cursor = self.db.execute("SELECT * FROM projects ORDER BY name")
        return [self._row_to_project(row) for row in cursor.fetchall()]

    def update(self, project: Project) -> Project:
        """Update a project."""
        project.updated_at = datetime.now()
        self.db.execute("""
            UPDATE projects SET name = ?, path = ?, description = ?, collection_id = ?, tags = ?, updated_at = ?, config = ?
            WHERE id = ?
        """, (
            project.name,
            project.path,
            project.description,
            project.collection_id or "general",
            json.dumps(project.tags) if project.tags else "[]",
            project.updated_at.isoformat(),
            json.dumps(project.config) if project.config else None,
            project.id,
        ))
        self.db.commit()
        return project

    def upsert(self, project: Project) -> Project:
        """Insert or update a project. Matches by name if id is not set."""
        existing = self.get(project.name) if not project.id else self.get_by_id(project.id)
        if existing:
            project.id = existing.id
            return self.update(project)
        return self.create(project)

    def delete(self, name_or_id: str) -> bool:
        """Delete a project by name or UUID."""
        cursor = self.db.execute(
            "DELETE FROM projects WHERE name = ? OR id = ?",
            (name_or_id, name_or_id),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def move_to_collection(self, project_id: str, collection_id: str) -> bool:
        """Move a project to a different collection."""
        cursor = self.db.execute(
            "UPDATE projects SET collection_id = ?, updated_at = ? WHERE id = ?",
            (collection_id, datetime.now().isoformat(), project_id),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def _row_to_project(self, row) -> Project:
        keys = row.keys()
        raw_tags = row["tags"] if "tags" in keys else "[]"
        return Project(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            description=row["description"] if "description" in keys else None,
            collection_id=row["collection_id"] if "collection_id" in keys else "general",
            tags=json.loads(raw_tags) if raw_tags else [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            config=json.loads(row["config"]) if row["config"] else None,
        )


class CollectionRepository:
    """Repository for collection operations."""

    def __init__(self):
        self.db = get_db("rdc")

    def create(self, collection: Collection) -> Collection:
        """Create a new collection. Generates UUID if not set."""
        if not collection.id:
            collection.id = str(uuid_mod.uuid4())
        self.db.execute("""
            INSERT INTO collections (id, name, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            collection.id,
            collection.name,
            collection.description,
            collection.sort_order,
            collection.created_at.isoformat(),
            collection.updated_at.isoformat(),
        ))
        self.db.commit()
        return collection

    def get(self, name_or_id: str) -> Optional[Collection]:
        """Get a collection by name or id."""
        cursor = self.db.execute(
            "SELECT * FROM collections WHERE id = ? OR name = ?",
            (name_or_id, name_or_id),
        )
        row = cursor.fetchone()
        return self._row_to_collection(row) if row else None

    def list(self) -> list[Collection]:
        """List all collections ordered by sort_order, then name."""
        cursor = self.db.execute(
            "SELECT * FROM collections ORDER BY sort_order, name"
        )
        return [self._row_to_collection(row) for row in cursor.fetchall()]

    def update(self, collection: Collection) -> Collection:
        """Update a collection."""
        collection.updated_at = datetime.now()
        self.db.execute("""
            UPDATE collections SET name = ?, description = ?, sort_order = ?, updated_at = ?
            WHERE id = ?
        """, (
            collection.name,
            collection.description,
            collection.sort_order,
            collection.updated_at.isoformat(),
            collection.id,
        ))
        self.db.commit()
        return collection

    def delete(self, collection_id: str) -> bool:
        """Delete a collection. Blocks deletion of 'general'. Moves orphan projects to 'general'."""
        if collection_id == "general":
            return False
        # Move orphan projects to general
        self.db.execute(
            "UPDATE projects SET collection_id = 'general' WHERE collection_id = ?",
            (collection_id,),
        )
        cursor = self.db.execute(
            "DELETE FROM collections WHERE id = ?", (collection_id,)
        )
        self.db.commit()
        return cursor.rowcount > 0

    def project_counts(self) -> dict[str, int]:
        """Get project count per collection."""
        cursor = self.db.execute("""
            SELECT collection_id, COUNT(*) as cnt
            FROM projects
            GROUP BY collection_id
        """)
        return {row["collection_id"]: row["cnt"] for row in cursor.fetchall()}

    def _row_to_collection(self, row) -> Collection:
        d = dict(row)
        return Collection(
            id=d["id"],
            name=d["name"],
            description=d.get("description"),
            sort_order=d.get("sort_order", 0),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else datetime.now(),
        )


class TaskRepository:
    """Repository for task operations."""

    def __init__(self):
        self.db = get_db("tasks")

    def create(
        self,
        project_id: str,
        description: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        metadata: Optional[dict] = None,
        depends_on: Optional[list[str]] = None,
        next_tasks: Optional[list[str]] = None,
        parent_task_id: Optional[str] = None,
    ) -> Task:
        """Create a new task."""
        # Check if blocked by dependencies
        initial_status = TaskStatus.PENDING
        if depends_on:
            for dep_id in depends_on:
                dep = self.get(dep_id)
                if not dep or dep.status != TaskStatus.COMPLETED:
                    initial_status = TaskStatus.BLOCKED
                    break

        project_name = _resolve_project_name(project_id)

        for _ in range(3):
            task = Task(
                id=secrets.token_hex(8),
                project_id=project_id,
                project=project_name,
                description=description,
                priority=priority,
                status=initial_status,
                metadata=metadata,
                depends_on=depends_on,
                next_tasks=next_tasks,
                parent_task_id=parent_task_id,
            )

            try:
                self.db.execute("""
                    INSERT INTO tasks (id, project_id, description, priority, status, created_at, metadata, depends_on, next_tasks, parent_task_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task.id,
                    task.project_id,
                    task.description,
                    task.priority.value,
                    task.status.value,
                    task.created_at.isoformat(),
                    json.dumps(task.metadata) if task.metadata else None,
                    json.dumps(task.depends_on) if task.depends_on else None,
                    json.dumps(task.next_tasks) if task.next_tasks else None,
                    task.parent_task_id,
                ))
                self.db.commit()
                return task
            except sqlite3.IntegrityError:
                continue

        raise RuntimeError("Failed to generate unique task ID after 3 attempts")

    def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        cursor = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def list(
        self,
        status: Optional[TaskStatus] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[Task]:
        """List tasks with optional filters."""
        conditions = []
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status.value)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)

        where = " AND ".join(conditions) if conditions else "1=1"

        cursor = self.db.execute(f"""
            SELECT * FROM tasks
            WHERE {where}
            ORDER BY
                CASE priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'normal' THEN 2
                    ELSE 3
                END,
                created_at
            LIMIT ?
        """, params + [limit])

        rows = cursor.fetchall()
        if not rows:
            return []

        # Batch resolve project names
        pids = list({dict(r)["project_id"] for r in rows if dict(r).get("project_id")})
        name_map = _resolve_project_names(pids)

        return [self._row_to_task(row, name_map) for row in rows]

    def list_pending(self, limit: int = 10) -> list[Task]:
        """List pending tasks ordered by priority."""
        return self.list(status=TaskStatus.PENDING, limit=limit)

    def claim_next(self, assigned_to: str) -> Optional[Task]:
        """Atomically claim the next pending task."""
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'in_progress',
                assigned_to = ?,
                started_at = ?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending'
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        ELSE 3
                    END,
                    created_at
                LIMIT 1
            )
            RETURNING *
        """, (assigned_to, datetime.now().isoformat()))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None
        return self._row_to_task(row)

    def start(self, task_id: str, agent: Optional[str] = None) -> Optional[Task]:
        """Start a task (mark as in_progress)."""
        now = datetime.now().isoformat()
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'in_progress', started_at = ?, assigned_to = COALESCE(?, assigned_to)
            WHERE id = ? AND status IN ('pending', 'blocked')
            RETURNING *
        """, (now, agent, task_id))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None
        return self._row_to_task(row)

    def block(self, task_id: str, reason: Optional[str] = None) -> Optional[Task]:
        """Block a task (needs human input)."""
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'blocked', review_prompt = COALESCE(?, review_prompt)
            WHERE id = ?
            RETURNING *
        """, (reason, task_id))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None
        return self._row_to_task(row)

    def request_review(self, task_id: str, review_prompt: Optional[str] = None) -> Optional[Task]:
        """Mark a task as awaiting review."""
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'awaiting_review',
                requires_review = 1,
                review_prompt = COALESCE(?, review_prompt)
            WHERE id = ?
            RETURNING *
        """, (review_prompt, task_id))
        row = cursor.fetchone()
        self.db.commit()
        return self._row_to_task(row) if row else None

    def approve(self, task_id: str, reviewer_id: str, modified_description: Optional[str] = None) -> Optional[Task]:
        """Approve a reviewed task, setting it back to pending."""
        now = datetime.now().isoformat()
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'pending',
                description = COALESCE(?, description),
                reviewed_by = ?,
                reviewed_at = ?
            WHERE id = ?
            RETURNING *
        """, (modified_description, reviewer_id, now, task_id))
        row = cursor.fetchone()
        self.db.commit()
        return self._row_to_task(row) if row else None

    def requeue(self, task_id: str, priority: str = "high") -> Optional[Task]:
        """Re-queue a task for execution (reset to pending, clear claim)."""
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'pending',
                priority = ?,
                claimed_by = NULL,
                claimed_at = NULL
            WHERE id = ?
            RETURNING *
        """, (priority, task_id))
        row = cursor.fetchone()
        self.db.commit()
        return self._row_to_task(row) if row else None

    def complete(
        self,
        task_id: str,
        result: Optional[str] = None,
        output: Optional[str] = None,
        output_artifacts: Optional[list[str]] = None,
    ) -> Optional[Task]:
        """Mark a task as completed and unblock dependent tasks."""
        now = datetime.now().isoformat()
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'completed', completed_at = ?, result = ?, output = ?, output_artifacts = ?
            WHERE id = ?
            RETURNING *
        """, (
            now,
            result,
            output,
            json.dumps(output_artifacts) if output_artifacts else None,
            task_id
        ))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None

        completed_task = self._row_to_task(row)

        # Unblock tasks that depend on this one
        self._unblock_dependents(task_id)

        return completed_task

    def _unblock_dependents(self, completed_task_id: str) -> None:
        """Check and unblock tasks that were waiting on this task."""
        cursor = self.db.execute("""
            SELECT * FROM tasks WHERE status = 'blocked'
        """)

        for row in cursor.fetchall():
            depends_on = json.loads(row["depends_on"]) if row["depends_on"] else []

            if completed_task_id in depends_on:
                all_complete = True
                for dep_id in depends_on:
                    dep = self.get(dep_id)
                    if not dep or dep.status != TaskStatus.COMPLETED:
                        all_complete = False
                        break

                if all_complete:
                    self.db.execute("""
                        UPDATE tasks SET status = 'pending' WHERE id = ?
                    """, (row["id"],))

        self.db.commit()

    def delete(self, task_id: str) -> bool:
        """Hard-delete a single task. Only allowed for completed/failed/cancelled."""
        task = self.get(task_id)
        if not task:
            return False
        if task.status.value not in ("completed", "failed", "cancelled"):
            raise ValueError(f"Cannot delete task with status '{task.status.value}'. Only completed/failed/cancelled tasks can be deleted.")
        cursor = self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def delete_batch(
        self,
        status: Optional[str] = None,
        project_id: Optional[str] = None,
        older_than_hours: Optional[int] = None,
    ) -> int:
        """Bulk delete finished tasks matching filters. Returns count deleted."""
        conditions = ["status IN ('completed', 'failed', 'cancelled')"]
        params: list = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if older_than_hours is not None:
            cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat()
            conditions.append("created_at < ?")
            params.append(cutoff)

        where = " AND ".join(conditions)
        cursor = self.db.execute(f"DELETE FROM tasks WHERE {where}", params)
        self.db.commit()
        return cursor.rowcount

    def get_output(self, task_id: str) -> Optional[str]:
        """Get the captured output of a completed task."""
        task = self.get(task_id)
        if task:
            return task.output
        return None

    def fail(self, task_id: str, error: str) -> Optional[Task]:
        """Mark a task as failed."""
        now = datetime.now().isoformat()

        task = self.get(task_id)
        if not task:
            return None

        new_retry = task.retry_count + 1
        new_status = TaskStatus.FAILED if new_retry >= task.max_retries else TaskStatus.PENDING

        cursor = self.db.execute("""
            UPDATE tasks
            SET status = ?,
                completed_at = CASE WHEN ? = 'failed' THEN ? ELSE completed_at END,
                error = ?,
                retry_count = ?,
                assigned_to = NULL,
                started_at = NULL
            WHERE id = ?
            RETURNING *
        """, (new_status.value, new_status.value, now, error, new_retry, task_id))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None
        return self._row_to_task(row)

    def cancel(self, task_id: str) -> Optional[Task]:
        """Cancel a task."""
        now = datetime.now().isoformat()
        cursor = self.db.execute("""
            UPDATE tasks
            SET status = 'cancelled', completed_at = ?
            WHERE id = ? AND status IN ('pending', 'blocked')
            RETURNING *
        """, (now, task_id))

        row = cursor.fetchone()
        self.db.commit()

        if not row:
            return None
        return self._row_to_task(row)

    def stats(self) -> dict:
        """Get task statistics."""
        cursor = self.db.execute("""
            SELECT
                status,
                COUNT(*) as count
            FROM tasks
            GROUP BY status
        """)

        stats = {s.value: 0 for s in TaskStatus}
        for row in cursor.fetchall():
            stats[row["status"]] = row["count"]

        stats["total"] = sum(stats.values())

        project_cursor = self.db.execute("""
            SELECT project_id, COUNT(*) as count
            FROM tasks
            WHERE status NOT IN ('completed', 'cancelled')
            GROUP BY project_id
        """)
        # Resolve project_id UUIDs to names for the stats
        raw = {row["project_id"]: row["count"] for row in project_cursor.fetchall()}
        name_map = _resolve_project_names(list(raw.keys()))
        stats["by_project"] = {
            name_map.get(pid, pid): count for pid, count in raw.items()
        }

        return stats

    def _row_to_task(self, row, name_map: Optional[dict[str, str]] = None) -> Task:
        row_dict = dict(row)
        project_id = row_dict.get("project_id", "")
        if name_map:
            project_name = name_map.get(project_id, "")
        else:
            project_name = _resolve_project_name(project_id)
        return Task(
            id=row_dict["id"],
            project_id=project_id,
            project=project_name,
            description=row_dict["description"],
            priority=TaskPriority(row_dict["priority"]),
            status=TaskStatus(row_dict["status"]),
            assigned_to=row_dict.get("assigned_to"),
            created_at=datetime.fromisoformat(row_dict["created_at"]),
            started_at=datetime.fromisoformat(row_dict["started_at"]) if row_dict.get("started_at") else None,
            completed_at=datetime.fromisoformat(row_dict["completed_at"]) if row_dict.get("completed_at") else None,
            result=row_dict.get("result"),
            error=row_dict.get("error"),
            retry_count=row_dict.get("retry_count", 0),
            max_retries=row_dict.get("max_retries", 3),
            metadata=json.loads(row_dict["metadata"]) if row_dict.get("metadata") else None,
            depends_on=json.loads(row_dict["depends_on"]) if row_dict.get("depends_on") else None,
            output=row_dict.get("output"),
            output_artifacts=json.loads(row_dict["output_artifacts"]) if row_dict.get("output_artifacts") else None,
            next_tasks=json.loads(row_dict["next_tasks"]) if row_dict.get("next_tasks") else None,
            requires_review=bool(row_dict.get("requires_review", 0)),
            review_prompt=row_dict.get("review_prompt"),
            reviewed_by=row_dict.get("reviewed_by"),
            reviewed_at=datetime.fromisoformat(row_dict["reviewed_at"]) if row_dict.get("reviewed_at") else None,
            claimed_by=row_dict.get("claimed_by"),
            claimed_at=datetime.fromisoformat(row_dict["claimed_at"]) if row_dict.get("claimed_at") else None,
            agent_pid=row_dict.get("agent_pid"),
            agent_log_path=row_dict.get("agent_log_path"),
            timeout_seconds=row_dict.get("timeout_seconds", 3600),
        )


class AgentRunRepository:
    """Repository for agent run history."""

    def __init__(self):
        self.db = get_db("logs")

    def create(self, run: AgentRun) -> AgentRun:
        """Create a new agent run record."""
        cursor = self.db.execute("""
            INSERT INTO agent_runs (project_id, provider, task, task_id, pid, started_at, status, log_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run.project_id,
            run.provider,
            run.task,
            run.task_id,
            run.pid,
            run.started_at.isoformat(),
            run.status.value,
            run.log_file,
        ))
        run.id = cursor.lastrowid
        self.db.commit()
        return run

    def update(self, run: AgentRun) -> AgentRun:
        """Update an agent run."""
        self.db.execute("""
            UPDATE agent_runs
            SET ended_at = ?, exit_code = ?, status = ?, error = ?
            WHERE id = ?
        """, (
            run.ended_at.isoformat() if run.ended_at else None,
            run.exit_code,
            run.status.value,
            run.error,
            run.id,
        ))
        self.db.commit()
        return run

    def get(self, run_id: int) -> Optional[AgentRun]:
        """Get a run by ID."""
        cursor = self.db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def get_active(self, project_id: str) -> Optional[AgentRun]:
        """Get the active run for a project."""
        cursor = self.db.execute("""
            SELECT * FROM agent_runs
            WHERE project_id = ? AND status = 'running'
            ORDER BY started_at DESC
            LIMIT 1
        """, (project_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def list(
        self,
        project_id: Optional[str] = None,
        status: Optional[AgentRunStatus] = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        """List agent runs."""
        conditions = []
        params = []

        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where = " AND ".join(conditions) if conditions else "1=1"

        cursor = self.db.execute(f"""
            SELECT * FROM agent_runs
            WHERE {where}
            ORDER BY started_at DESC
            LIMIT ?
        """, params + [limit])

        rows = cursor.fetchall()
        if not rows:
            return []

        pids = list({dict(r)["project_id"] for r in rows if dict(r).get("project_id")})
        name_map = _resolve_project_names(pids)

        return [self._row_to_run(row, name_map) for row in rows]

    def _row_to_run(self, row, name_map: Optional[dict[str, str]] = None) -> AgentRun:
        project_id = row["project_id"]
        if name_map:
            project_name = name_map.get(project_id, "")
        else:
            project_name = _resolve_project_name(project_id)
        return AgentRun(
            id=row["id"],
            project_id=project_id,
            project=project_name,
            provider=row["provider"],
            task=row["task"],
            task_id=row["task_id"],
            pid=row["pid"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            exit_code=row["exit_code"],
            status=AgentRunStatus(row["status"]),
            error=row["error"],
            log_file=row["log_file"],
        )


class EventRepository:
    """Repository for event logging."""

    def __init__(self):
        self.db = get_db("logs")

    def log(
        self,
        event_type: str,
        project_id: Optional[str] = None,
        project: Optional[str] = None,
        agent: Optional[str] = None,
        task_id: Optional[str] = None,
        level: EventLevel = EventLevel.INFO,
        message: Optional[str] = None,
        data: Optional[dict] = None,
    ) -> Event:
        """Log an event.

        Accepts project_id (UUID) directly, or project (name) which will
        be resolved to a UUID. If both are provided, project_id takes precedence.
        """
        if not project_id and project:
            project_id = resolve_project_id(project)

        event = Event(
            type=event_type,
            project_id=project_id,
            project=project or _resolve_project_name(project_id) if project_id else None,
            agent=agent,
            task_id=task_id,
            level=level,
            message=message,
            data=data,
        )

        cursor = self.db.execute("""
            INSERT INTO events (timestamp, type, project_id, agent, task_id, level, message, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.timestamp.isoformat(),
            event.type,
            event.project_id,
            event.agent,
            event.task_id,
            event.level.value,
            event.message,
            json.dumps(event.data) if event.data else None,
        ))
        event.id = cursor.lastrowid
        self.db.commit()
        return event

    def query(
        self,
        event_type: Optional[str] = None,
        project_id: Optional[str] = None,
        level: Optional[EventLevel] = None,
        since: Optional[datetime] = None,
        before: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events."""
        conditions = []
        params = []

        if event_type:
            conditions.append("type = ?")
            params.append(event_type)
        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if level:
            conditions.append("level = ?")
            params.append(level.value)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        if before:
            conditions.append("timestamp < ?")
            params.append(before.isoformat())

        where = " AND ".join(conditions) if conditions else "1=1"

        cursor = self.db.execute(f"""
            SELECT * FROM events
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """, params + [limit])

        rows = cursor.fetchall()
        if not rows:
            return []

        pids = list({dict(r)["project_id"] for r in rows if dict(r).get("project_id")})
        name_map = _resolve_project_names(pids)

        return [self._row_to_event(row, name_map) for row in rows]

    def _row_to_event(self, row, name_map: Optional[dict[str, str]] = None) -> Event:
        project_id = row["project_id"]
        if name_map:
            project_name = name_map.get(project_id, "") if project_id else None
        else:
            project_name = _resolve_project_name(project_id) if project_id else None
        return Event(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            type=row["type"],
            project_id=project_id,
            project=project_name,
            agent=row["agent"],
            task_id=row["task_id"],
            level=EventLevel(row["level"]),
            message=row["message"],
            data=json.loads(row["data"]) if row["data"] else None,
        )


# =============================================================================
# PROCESS CONFIG REPOSITORY (process_configs table — config + runtime state)
# =============================================================================

class ProcessConfigRepository:
    """Repository for process state (config + runtime)."""

    def __init__(self):
        self.db = get_db("rdc")

    def upsert(self, state: ProcessConfig) -> ProcessConfig:
        """Insert or update a process config/state."""
        # If a row with the same (project_id, name) exists but a different id,
        # reuse the existing id to avoid UNIQUE constraint violation.
        existing = self.db.execute(
            "SELECT id FROM process_configs WHERE project_id = ? AND name = ?",
            (state.project_id, state.name),
        ).fetchone()
        if existing and existing[0] != state.id:
            state.id = existing[0]

        self.db.execute("""
            INSERT INTO process_configs
                (id, project_id, name, command, cwd, port, description,
                 process_type, status, pid, started_at, completed_at,
                 exit_code, error, discovered_by, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                command = excluded.command,
                cwd = excluded.cwd,
                port = excluded.port,
                description = excluded.description,
                process_type = excluded.process_type,
                status = excluded.status,
                pid = excluded.pid,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                exit_code = excluded.exit_code,
                error = excluded.error,
                kind = excluded.kind
        """, (
            state.id,
            state.project_id,
            state.name,
            state.command,
            state.cwd,
            state.port,
            state.description,
            state.process_type.value,
            state.status.value,
            state.pid,
            state.started_at.isoformat() if state.started_at else None,
            state.completed_at.isoformat() if state.completed_at else None,
            state.exit_code,
            state.error,
            state.discovered_by,
            state.kind.value,
        ))
        self.db.commit()
        return state

    def update_status(
        self,
        process_id: str,
        status: str,
        pid: Optional[int] = None,
        exit_code: Optional[int] = None,
        error: Optional[str] = None,
        started_at: Optional[str] = None,
    ):
        """Lightweight update for runtime fields only."""
        self.db.execute("""
            UPDATE process_configs
            SET status = ?, pid = ?, exit_code = ?, error = ?, started_at = ?
            WHERE id = ?
        """, (status, pid, exit_code, error, started_at, process_id))
        self.db.commit()

    def get(self, process_id: str) -> Optional[ProcessConfig]:
        cursor = self.db.execute("SELECT * FROM process_configs WHERE id = ?", (process_id,))
        row = cursor.fetchone()
        return self._row_to_state(row) if row else None

    def list(self, project_id: Optional[str] = None) -> list[ProcessConfig]:
        if project_id:
            cursor = self.db.execute(
                "SELECT * FROM process_configs WHERE project_id = ? ORDER BY name",
                (project_id,),
            )
        else:
            cursor = self.db.execute("SELECT * FROM process_configs ORDER BY name")
        rows = cursor.fetchall()
        if not rows:
            return []
        pids = list({dict(r)["project_id"] for r in rows if dict(r).get("project_id")})
        name_map = _resolve_project_names(pids)
        return [self._row_to_state(row, name_map) for row in rows]

    def delete(self, process_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM process_configs WHERE id = ?", (process_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def delete_by_project(self, project_id: str) -> int:
        cursor = self.db.execute("DELETE FROM process_configs WHERE project_id = ?", (project_id,))
        self.db.commit()
        return cursor.rowcount

    def _row_to_state(self, row, name_map: Optional[dict[str, str]] = None) -> ProcessConfig:
        d = dict(row)
        project_id = d.get("project_id", "")
        if name_map:
            project_name = name_map.get(project_id, "")
        else:
            project_name = _resolve_project_name(project_id)
        return ProcessConfig(
            id=d["id"],
            project_id=project_id,
            project=project_name,
            name=d["name"],
            kind=ActionKind(d["kind"]) if d.get("kind") else ActionKind.SERVICE,
            process_type=ProcessType(d.get("process_type") or "dev_server"),
            command=d["command"],
            cwd=d.get("cwd") or "",
            port=d.get("port"),
            description=d.get("description"),
            status=ProcessStatus(d.get("status") or "idle"),
            pid=d.get("pid"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
            exit_code=d.get("exit_code"),
            error=d.get("error"),
            discovered_at=datetime.fromisoformat(d["discovered_at"]) if d.get("discovered_at") else datetime.now(),
            discovered_by=d.get("discovered_by") or "manual",
        )


# =============================================================================
# AGENT STATE REPOSITORY (agent_registry table)
# =============================================================================

class AgentStateRepository:
    """Repository for agent runtime state."""

    def __init__(self):
        self.db = get_db("rdc")

    def upsert(self, state: AgentState) -> AgentState:
        """Insert or update agent state."""
        self.db.execute("""
            INSERT INTO agent_registry
                (project_id, provider, preferred_worktree, config,
                 status, pid, worktree, current_task, started_at,
                 last_activity, error, retry_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                provider = excluded.provider,
                status = excluded.status,
                pid = excluded.pid,
                worktree = excluded.worktree,
                current_task = excluded.current_task,
                started_at = excluded.started_at,
                last_activity = excluded.last_activity,
                error = excluded.error,
                retry_count = excluded.retry_count
        """, (
            state.project_id,
            state.provider,
            state.preferred_worktree,
            json.dumps(state.config) if state.config else None,
            state.status.value,
            state.pid,
            state.worktree,
            state.current_task,
            state.started_at.isoformat() if state.started_at else None,
            state.last_activity.isoformat() if state.last_activity else None,
            state.error,
            state.retry_count,
        ))
        self.db.commit()
        return state

    def update_status(self, project_id: str, status: str, **kwargs):
        """Lightweight status update."""
        sets = ["status = ?"]
        params = [status]
        for key in ("pid", "current_task", "error", "retry_count", "worktree"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                params.append(kwargs[key])
        if "last_activity" in kwargs:
            sets.append("last_activity = ?")
            val = kwargs["last_activity"]
            params.append(val.isoformat() if isinstance(val, datetime) else val)
        if "started_at" in kwargs:
            sets.append("started_at = ?")
            val = kwargs["started_at"]
            params.append(val.isoformat() if isinstance(val, datetime) else val)
        params.append(project_id)
        self.db.execute(
            f"UPDATE agent_registry SET {', '.join(sets)} WHERE project_id = ?",
            params,
        )
        self.db.commit()

    def get(self, project_id: str) -> Optional[AgentState]:
        cursor = self.db.execute("SELECT * FROM agent_registry WHERE project_id = ?", (project_id,))
        row = cursor.fetchone()
        return self._row_to_state(row) if row else None

    def get_by_name(self, project_name: str) -> Optional[AgentState]:
        """Get agent state by project name (resolves to UUID internally)."""
        pid = resolve_project_id(project_name)
        if not pid:
            return None
        return self.get(pid)

    def list(self) -> list[AgentState]:
        cursor = self.db.execute("SELECT * FROM agent_registry")
        rows = cursor.fetchall()
        if not rows:
            return []
        pids = list({dict(r)["project_id"] for r in rows})
        name_map = _resolve_project_names(pids)
        return [self._row_to_state(row, name_map) for row in rows]

    def delete(self, project_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM agent_registry WHERE project_id = ?", (project_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def _row_to_state(self, row, name_map: Optional[dict[str, str]] = None) -> AgentState:
        d = dict(row)
        project_id = d["project_id"]
        if name_map:
            project_name = name_map.get(project_id, "")
        else:
            project_name = _resolve_project_name(project_id)
        return AgentState(
            project_id=project_id,
            project=project_name,
            provider=d.get("provider") or "cursor",
            preferred_worktree=d.get("preferred_worktree"),
            config=json.loads(d["config"]) if d.get("config") else None,
            status=AgentStatus(d.get("status") or "idle"),
            pid=d.get("pid"),
            worktree=d.get("worktree"),
            current_task=d.get("current_task"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            last_activity=datetime.fromisoformat(d["last_activity"]) if d.get("last_activity") else None,
            error=d.get("error"),
            retry_count=d.get("retry_count") or 0,
        )


# =============================================================================
# PORT ASSIGNMENT REPOSITORY (port_assignments table)
# =============================================================================

class PortAssignmentRepository:
    """Repository for port assignments."""

    def __init__(self):
        self.db = get_db("rdc")

    def upsert(self, project_id: str, service: str, port: int) -> PortAssignment:
        """Assign or reassign a port."""
        self.db.execute("""
            INSERT INTO port_assignments (project_id, service, port, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, service) DO UPDATE SET
                port = excluded.port
        """, (project_id, service, port, datetime.now().isoformat()))
        self.db.commit()
        return PortAssignment(project_id=project_id, service=service, port=port)

    def get(self, project_id: str, service: str) -> Optional[PortAssignment]:
        cursor = self.db.execute(
            "SELECT * FROM port_assignments WHERE project_id = ? AND service = ?",
            (project_id, service),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return PortAssignment(id=row["id"], project_id=row["project_id"], service=row["service"], port=row["port"])

    def list(self, project_id: Optional[str] = None) -> list[PortAssignment]:
        if project_id:
            cursor = self.db.execute(
                "SELECT * FROM port_assignments WHERE project_id = ? ORDER BY service",
                (project_id,),
            )
        else:
            cursor = self.db.execute("SELECT * FROM port_assignments ORDER BY project_id, service")
        return [
            PortAssignment(id=r["id"], project_id=r["project_id"], service=r["service"], port=r["port"])
            for r in cursor.fetchall()
        ]

    def delete(self, project_id: str, service: str) -> bool:
        cursor = self.db.execute(
            "DELETE FROM port_assignments WHERE project_id = ? AND service = ?",
            (project_id, service),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def used_ports(self) -> set[int]:
        cursor = self.db.execute("SELECT DISTINCT port FROM port_assignments")
        return {row["port"] for row in cursor.fetchall()}


# =============================================================================
# VNC SESSION REPOSITORY (vnc_sessions table)
# =============================================================================

class VNCSessionRepository:
    """Repository for VNC sessions."""

    def __init__(self):
        self.db = get_db("rdc")

    def upsert(self, session: VNCSession) -> VNCSession:
        self.db.execute("""
            INSERT INTO vnc_sessions
                (id, process_id, target_url, vnc_port, web_port,
                 container_id, status, started_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                container_id = excluded.container_id,
                status = excluded.status,
                started_at = excluded.started_at,
                error = excluded.error
        """, (
            session.id,
            session.process_id,
            session.target_url,
            session.vnc_port,
            session.web_port,
            session.container_id,
            session.status.value,
            session.started_at.isoformat() if session.started_at else None,
            session.error,
        ))
        self.db.commit()
        return session

    def update_status(
        self,
        session_id: str,
        status: str,
        container_id: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.db.execute("""
            UPDATE vnc_sessions SET status = ?, container_id = ?, error = ?
            WHERE id = ?
        """, (status, container_id, error, session_id))
        self.db.commit()

    def get(self, session_id: str) -> Optional[VNCSession]:
        cursor = self.db.execute("SELECT * FROM vnc_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        return self._row_to_session(row) if row else None

    def get_by_process(self, process_id: str) -> Optional[VNCSession]:
        cursor = self.db.execute("SELECT * FROM vnc_sessions WHERE process_id = ?", (process_id,))
        row = cursor.fetchone()
        return self._row_to_session(row) if row else None

    def list(self) -> list[VNCSession]:
        cursor = self.db.execute("SELECT * FROM vnc_sessions")
        return [self._row_to_session(row) for row in cursor.fetchall()]

    def delete(self, session_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM vnc_sessions WHERE id = ?", (session_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def _row_to_session(self, row) -> VNCSession:
        d = dict(row)
        return VNCSession(
            id=d["id"],
            process_id=d["process_id"],
            target_url=d["target_url"],
            vnc_port=d["vnc_port"],
            web_port=d["web_port"],
            container_id=d.get("container_id"),
            status=VNCStatus(d.get("status") or "starting"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            error=d.get("error"),
        )


# =============================================================================
# AGENT SESSION REPOSITORY (agent_sessions table)
# =============================================================================

class AgentSessionRepository:
    """Repository for captured cursor-agent session IDs."""

    def __init__(self):
        self.db = get_db("rdc")

    def create(self, project_id: str, agent_session_id: str, label: Optional[str] = None) -> AgentSession:
        """Insert or update (on conflict) an agent session."""
        now = datetime.now().isoformat()
        cursor = self.db.execute("""
            INSERT INTO agent_sessions (project_id, agent_session_id, created_at, label)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, agent_session_id) DO UPDATE SET
                created_at = excluded.created_at,
                label = COALESCE(excluded.label, agent_sessions.label)
        """, (project_id, agent_session_id, now, label))
        self.db.commit()
        return AgentSession(
            id=cursor.lastrowid,
            project_id=project_id,
            agent_session_id=agent_session_id,
            created_at=datetime.fromisoformat(now),
            label=label,
        )

    def list_by_project(self, project_id: str, limit: int = 20) -> list[AgentSession]:
        """List sessions for a project, most recent first."""
        cursor = self.db.execute("""
            SELECT * FROM agent_sessions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (project_id, limit))
        return [self._row_to_session(row) for row in cursor.fetchall()]

    def get_latest(self, project_id: str) -> Optional[AgentSession]:
        """Get the most recent session for a project."""
        cursor = self.db.execute("""
            SELECT * FROM agent_sessions
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (project_id,))
        row = cursor.fetchone()
        return self._row_to_session(row) if row else None

    def delete(self, session_id: int) -> bool:
        """Delete a session by DB id."""
        cursor = self.db.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def _row_to_session(self, row) -> AgentSession:
        d = dict(row)
        return AgentSession(
            id=d["id"],
            project_id=d["project_id"],
            agent_session_id=d["agent_session_id"],
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
            label=d.get("label"),
        )


# =============================================================================
# RECIPE REPOSITORY
# =============================================================================

class RecipeRepository:
    """Repository for user-created recipe operations."""

    def __init__(self):
        self.db = get_db("rdc")

    def create(self, recipe: RecipeModel) -> RecipeModel:
        """Create a new recipe. Generates UUID if not set."""
        if not recipe.id:
            recipe.id = str(uuid_mod.uuid4())
        self.db.execute("""
            INSERT INTO recipes (id, name, description, prompt_template, model, inputs, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            recipe.id,
            recipe.name,
            recipe.description,
            recipe.prompt_template,
            recipe.model,
            json.dumps(recipe.inputs) if recipe.inputs else None,
            json.dumps(recipe.tags) if recipe.tags else None,
            recipe.created_at.isoformat(),
            recipe.updated_at.isoformat(),
        ))
        self.db.commit()
        return recipe

    def get(self, recipe_id: str) -> Optional[RecipeModel]:
        """Get a recipe by id."""
        cursor = self.db.execute(
            "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
        )
        row = cursor.fetchone()
        return self._row_to_model(row) if row else None

    def list(self) -> list[RecipeModel]:
        """List all user recipes ordered by name."""
        cursor = self.db.execute(
            "SELECT * FROM recipes ORDER BY name"
        )
        return [self._row_to_model(row) for row in cursor.fetchall()]

    def update(self, recipe: RecipeModel) -> RecipeModel:
        """Update a recipe."""
        recipe.updated_at = datetime.now()
        self.db.execute("""
            UPDATE recipes SET name = ?, description = ?, prompt_template = ?,
                model = ?, inputs = ?, tags = ?, updated_at = ?
            WHERE id = ?
        """, (
            recipe.name,
            recipe.description,
            recipe.prompt_template,
            recipe.model,
            json.dumps(recipe.inputs) if recipe.inputs else None,
            json.dumps(recipe.tags) if recipe.tags else None,
            recipe.updated_at.isoformat(),
            recipe.id,
        ))
        self.db.commit()
        return recipe

    def delete(self, recipe_id: str) -> bool:
        """Delete a recipe by id."""
        cursor = self.db.execute(
            "DELETE FROM recipes WHERE id = ?", (recipe_id,)
        )
        self.db.commit()
        return cursor.rowcount > 0

    def _row_to_model(self, row) -> RecipeModel:
        d = dict(row)
        return RecipeModel(
            id=d["id"],
            name=d["name"],
            description=d.get("description"),
            prompt_template=d["prompt_template"],
            model=d.get("model"),
            inputs=json.loads(d["inputs"]) if d.get("inputs") else None,
            tags=json.loads(d["tags"]) if d.get("tags") else None,
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else datetime.now(),
        )


# =============================================================================
# SINGLETON ACCESSORS
# =============================================================================

_collection_repo: Optional[CollectionRepository] = None
_task_repo: Optional[TaskRepository] = None
_project_repo: Optional[ProjectRepository] = None
_event_repo: Optional[EventRepository] = None
_agent_run_repo: Optional[AgentRunRepository] = None
_process_config_repo: Optional[ProcessConfigRepository] = None
_agent_state_repo: Optional[AgentStateRepository] = None
_port_repo: Optional[PortAssignmentRepository] = None
_vnc_repo: Optional[VNCSessionRepository] = None
_agent_session_repo: Optional[AgentSessionRepository] = None
_recipe_repo: Optional[RecipeRepository] = None


def get_collection_repo() -> CollectionRepository:
    global _collection_repo
    if _collection_repo is None:
        _collection_repo = CollectionRepository()
    return _collection_repo


def get_task_repo() -> TaskRepository:
    global _task_repo
    if _task_repo is None:
        _task_repo = TaskRepository()
    return _task_repo


def get_project_repo() -> ProjectRepository:
    global _project_repo
    if _project_repo is None:
        _project_repo = ProjectRepository()
    return _project_repo


def get_event_repo() -> EventRepository:
    global _event_repo
    if _event_repo is None:
        _event_repo = EventRepository()
    return _event_repo


def get_agent_run_repo() -> AgentRunRepository:
    global _agent_run_repo
    if _agent_run_repo is None:
        _agent_run_repo = AgentRunRepository()
    return _agent_run_repo


def get_process_config_repo() -> ProcessConfigRepository:
    global _process_config_repo
    if _process_config_repo is None:
        _process_config_repo = ProcessConfigRepository()
    return _process_config_repo


def get_agent_state_repo() -> AgentStateRepository:
    global _agent_state_repo
    if _agent_state_repo is None:
        _agent_state_repo = AgentStateRepository()
    return _agent_state_repo


def get_port_repo() -> PortAssignmentRepository:
    global _port_repo
    if _port_repo is None:
        _port_repo = PortAssignmentRepository()
    return _port_repo


def get_vnc_repo() -> VNCSessionRepository:
    global _vnc_repo
    if _vnc_repo is None:
        _vnc_repo = VNCSessionRepository()
    return _vnc_repo


def get_agent_session_repo() -> AgentSessionRepository:
    global _agent_session_repo
    if _agent_session_repo is None:
        _agent_session_repo = AgentSessionRepository()
    return _agent_session_repo


def get_recipe_repo() -> RecipeRepository:
    global _recipe_repo
    if _recipe_repo is None:
        _recipe_repo = RecipeRepository()
    return _recipe_repo
