"""Email channel for RDC Command Center.

Polls an IMAP inbox for emails, routes them through the orchestrator,
maintains bidirectional email threads with state tracking, and sends
SMTP replies on task completion/failure.
"""

import asyncio
import email as email_lib
import email.utils
import imaplib
import json
import logging
import os
import re
import secrets
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Callable, Awaitable, Optional

from ..config import EmailConfig, get_rdc_home
from ..db.repositories import get_email_thread_repo, EmailThreadRepository
from ..db.models import EmailDirection, EmailThreadStatus

logger = logging.getLogger(__name__)

# Regex to extract [project-name] tags from subject
PROJECT_TAG_RE = re.compile(r"\[([a-zA-Z0-9_-]+)\]")


def _sanitize_message_id(value: str | None) -> str | None:
    """Strip CRLF from message IDs to prevent SMTP header injection."""
    if not value:
        return None
    if "\r" in value or "\n" in value:
        return None
    return value.strip()


class EmailChannel:
    """IMAP-polling email channel with thread management and SMTP replies."""

    def __init__(
        self,
        config: EmailConfig,
        on_message: Optional[Callable[[dict], Awaitable[dict]]] = None,
    ):
        self.config = config
        self.on_message = on_message  # async callback: email_context -> orchestrator result
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._imap: Optional[imaplib.IMAP4_SSL | imaplib.IMAP4] = None
        self._attachments_dir = get_rdc_home() / "email-attachments"
        self._repo = get_email_thread_repo()
        self._allowed_senders_lower = {s.lower() for s in config.allowed_senders} if config.allowed_senders else set()

    async def start(self):
        """Start the email polling loop."""
        if self._running:
            return
        self._running = True
        self._attachments_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Email channel started (polling %s every %ds)",
            self.config.imap_host, self.config.poll_interval,
        )

    async def stop(self):
        """Stop the email polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._disconnect_imap()
        logger.info("Email channel stopped")

    # ── IMAP Polling ─────────────────────────────────────────────────────

    async def _poll_loop(self):
        """Main polling loop — check for unseen emails on interval."""
        while self._running:
            try:
                await self._check_inbox()
            except Exception:
                logger.exception("Email poll failed")
                self._disconnect_imap()
            await asyncio.sleep(self.config.poll_interval)

    def _connect_imap(self) -> imaplib.IMAP4_SSL | imaplib.IMAP4:
        if self._imap:
            try:
                self._imap.noop()
                return self._imap
            except Exception:
                self._disconnect_imap()

        # Use SSL for standard ports (993), STARTTLS for others (e.g., Proton Bridge 1143)
        if self.config.imap_port == 993:
            self._imap = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        else:
            self._imap = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)
            self._imap.starttls()
        self._imap.login(self.config.username, self.config.password)
        return self._imap

    def _disconnect_imap(self):
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    async def _check_inbox(self):
        """Check for unseen emails and process them."""
        imap = await asyncio.to_thread(self._connect_imap)
        await asyncio.to_thread(imap.select, "INBOX")

        _, data = await asyncio.to_thread(imap.search, None, "UNSEEN")
        msg_nums = data[0].split()

        if not msg_nums:
            return

        # Rate limit: process at most 10 emails per poll cycle
        max_per_poll = 10
        if len(msg_nums) > max_per_poll:
            logger.warning(
                "Found %d unseen emails, processing first %d",
                len(msg_nums), max_per_poll,
            )
            msg_nums = msg_nums[:max_per_poll]
        else:
            logger.info("Found %d unseen email(s)", len(msg_nums))

        for num in msg_nums:
            try:
                _, msg_data = await asyncio.to_thread(imap.fetch, num, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw_email)

                await self._process_email(msg)

                # Mark as seen
                await asyncio.to_thread(imap.store, num, "+FLAGS", "\\Seen")
            except Exception:
                logger.exception("Failed to process email #%s", num)

    # ── Email Processing ─────────────────────────────────────────────────

    async def _process_email(self, msg: email_lib.message.Message):
        """Parse an email, match/create thread, route through orchestrator."""
        from_addr = email_lib.utils.parseaddr(msg["From"])[1]
        to_addr = email_lib.utils.parseaddr(msg["To"])[1]
        subject = msg["Subject"] or ""
        message_id = msg["Message-ID"] or f"<{secrets.token_hex(12)}@rdc>"
        in_reply_to = msg["In-Reply-To"]
        references = msg["References"]

        # Validate parsed addresses — parseaddr returns empty string on failure
        if not from_addr or "@" not in from_addr:
            logger.warning("Rejecting email with invalid/empty From address")
            return
        if not to_addr:
            to_addr = self.config.username or ""

        # Sanitize message IDs — reject CRLF to prevent header injection.
        # If the sanitizer rejects the value, synthesize a CRLF-safe fallback so
        # downstream code (_extract_attachments, _repo.message_exists, etc.)
        # always has a valid string; otherwise the email gets stuck unseen and
        # re-fetched every poll cycle.
        message_id = _sanitize_message_id(message_id) or f"<{secrets.token_hex(12)}@rdc>"
        in_reply_to = _sanitize_message_id(in_reply_to) if in_reply_to else None
        if references:
            references = " ".join(
                _sanitize_message_id(r) for r in references.split() if _sanitize_message_id(r)
            )

        # Security: only process from allowed senders
        if self._allowed_senders_lower:
            if from_addr.lower() not in self._allowed_senders_lower:
                logger.warning("Ignoring email from unauthorized sender: %s", from_addr)
                return

        # Skip if we've already processed this Message-ID
        if self._repo.message_exists(message_id):
            logger.debug("Skipping already-processed message: %s", message_id)
            return

        # Extract body
        body_text, body_html = self._extract_body(msg)

        # Extract attachments
        attachments = await self._extract_attachments(msg, message_id)

        # Extract project tags from subject: [project-name]
        tags = PROJECT_TAG_RE.findall(subject)
        # Clean subject (remove tags for display)
        clean_subject = PROJECT_TAG_RE.sub("", subject).strip()

        # Find or create thread
        thread = self._match_thread(in_reply_to, references)
        if thread:
            # Update thread status for follow-up
            if thread.status == EmailThreadStatus.WAITING:
                self._repo.update_thread_status(thread.id, EmailThreadStatus.ONGOING)
            elif thread.status == EmailThreadStatus.CLOSED:
                # Reopen closed thread on new message
                self._repo.update_thread_status(thread.id, EmailThreadStatus.ONGOING)
        else:
            # Resolve project from tags or default
            project_id = await self._resolve_project(tags)
            thread = self._repo.create_thread(
                from_address=from_addr,
                subject=clean_subject,
                project_id=project_id,
                tags=tags,
            )
            logger.info("Created email thread %s: %s", thread.id, clean_subject)

        # Store message in thread
        attachment_dicts = [
            {"filename": a["filename"], "path": a["path"],
             "size_bytes": a["size_bytes"], "content_type": a["content_type"]}
            for a in attachments
        ]
        email_msg = self._repo.add_message(
            thread_id=thread.id,
            message_id=message_id,
            direction=EmailDirection.INBOUND,
            from_address=from_addr,
            to_address=to_addr,
            subject=clean_subject,
            body_text=body_text,
            body_html=body_html,
            in_reply_to=in_reply_to,
            attachments=attachment_dicts,
        )

        # Race condition: another poller already inserted this message
        if email_msg is None:
            logger.debug("Message %s already inserted (race), skipping", message_id)
            return

        # Build context for orchestrator
        thread_messages = self._repo.get_thread_messages(thread.id)
        email_context = {
            "channel": "email",
            "thread_id": thread.id,
            "thread_status": thread.status.value,
            "thread_subject": thread.subject,
            "thread_condensed_context": thread.condensed_context,
            "thread_message_count": len(thread_messages),
            "thread_tags": thread.tags,
            "thread_project_id": thread.project_id,
            "thread_task_ids": thread.task_ids,
            # Current message
            "message_id": email_msg.id,
            "from": from_addr,
            "subject": clean_subject,
            "body": body_text,
            "attachments": attachment_dicts,
            # Thread history (last 10 messages for context)
            "thread_history": [
                {
                    "direction": m.direction.value,
                    "from": m.from_address,
                    "subject": m.subject,
                    "body_preview": (m.body_text or "")[:500],
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "has_attachments": len(m.attachments) > 0,
                }
                for m in thread_messages[-10:]
            ],
        }

        # Route through orchestrator
        if self.on_message:
            try:
                result = await self.on_message(email_context)
                self._repo.mark_processed(email_msg.id, task_id=result.get("task_id"))

                # Link task to thread if one was created
                if result.get("task_id"):
                    self._repo.link_task(thread.id, result["task_id"])

                # Update condensed context if the orchestrator produced one
                if result.get("condensed_context"):
                    self._repo.update_condensed_context(
                        thread.id, result["condensed_context"]
                    )

                # Update project if orchestrator resolved it
                if result.get("project_id") and not thread.project_id:
                    self._repo.set_project(thread.id, result["project_id"])

                logger.info(
                    "Processed email in thread %s → task %s",
                    thread.id, result.get("task_id", "none"),
                )
            except Exception:
                logger.exception("Orchestrator processing failed for thread %s", thread.id)

    def _match_thread(
        self, in_reply_to: str | None, references: str | None,
    ):
        """Match an email to an existing thread via In-Reply-To or References."""
        repo = self._repo

        # Check In-Reply-To first (most specific)
        if in_reply_to:
            thread = repo.find_thread_by_message_id(in_reply_to.strip())
            if thread:
                return thread

        # Check References header (space-separated list of Message-IDs)
        if references:
            for ref in reversed(references.split()):
                ref = ref.strip()
                if ref:
                    thread = repo.find_thread_by_message_id(ref)
                    if thread:
                        return thread

        return None

    # ── SMTP Replies ─────────────────────────────────────────────────────

    async def send_reply(
        self,
        thread_id: str,
        body_text: str,
        subject_prefix: str = "Re: ",
    ) -> bool:
        """Send an SMTP reply in the context of an existing thread."""
        thread = self._repo.get_thread(thread_id)
        if not thread:
            logger.error("Cannot reply: thread %s not found", thread_id)
            return False

        messages = self._repo.get_thread_messages(thread_id)
        if not messages:
            logger.error("Cannot reply: thread %s has no messages", thread_id)
            return False

        # Use the most recent inbound message for reply headers
        last_inbound = None
        for m in reversed(messages):
            if m.direction == EmailDirection.INBOUND:
                last_inbound = m
                break
        if not last_inbound:
            last_inbound = messages[0]

        from_addr = self.config.from_address or self.config.username
        to_addr = thread.from_address
        subject = f"{subject_prefix}{thread.subject}"

        # Build email with proper threading headers
        mime_msg = MIMEMultipart("alternative")
        mime_msg["From"] = from_addr
        mime_msg["To"] = to_addr
        mime_msg["Subject"] = subject
        # Sanitize headers from DB to prevent CRLF injection
        safe_reply_to = _sanitize_message_id(last_inbound.message_id)
        if safe_reply_to:
            mime_msg["In-Reply-To"] = safe_reply_to
        # Build References chain
        ref_ids = [_sanitize_message_id(m.message_id) for m in messages if m.message_id]
        ref_ids = [r for r in ref_ids if r]
        if ref_ids:
            mime_msg["References"] = " ".join(ref_ids)

        # Generate a Message-ID for our reply
        our_message_id = f"<rdc-{secrets.token_hex(12)}@{self.config.imap_host}>"
        mime_msg["Message-ID"] = our_message_id

        mime_msg.attach(MIMEText(body_text, "plain"))

        try:
            await asyncio.to_thread(self._send_smtp, mime_msg)

            # Store outbound message in thread
            self._repo.add_message(
                thread_id=thread_id,
                message_id=our_message_id,
                direction=EmailDirection.OUTBOUND,
                from_address=from_addr,
                to_address=to_addr,
                subject=subject,
                body_text=body_text,
                in_reply_to=last_inbound.message_id,
            )

            # Update thread status to waiting (we replied, waiting for user)
            self._repo.update_thread_status(thread_id, EmailThreadStatus.WAITING)

            logger.info("Sent reply in thread %s to %s", thread_id, to_addr)
            return True
        except Exception:
            logger.exception("Failed to send reply for thread %s", thread_id)
            return False

    def _send_smtp(self, msg: MIMEMultipart):
        """Send an email via SMTP (blocking — call from thread)."""
        # Port 465 is SMTPS (implicit TLS); everything else (587, 1025, 2525)
        # gets a plaintext connect + STARTTLS upgrade.
        if self.config.smtp_port == 465:
            with smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port, timeout=30) as server:
                server.login(self.config.username, self.config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(self.config.username, self.config.password)
                server.send_message(msg)

    # ── Attachment Handling ───────────────────────────────────────────────

    async def _extract_attachments(
        self, msg: email_lib.message.Message, message_id: str,
    ) -> list[dict]:
        """Save attachments to disk and return metadata."""
        attachments = []
        max_bytes = self.config.max_attachment_size_mb * 1024 * 1024

        # Sanitize message_id for directory name
        safe_id = re.sub(r"[<>@/\\]", "_", message_id)
        attach_dir = self._attachments_dir / safe_id

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            filename = part.get_filename()
            if not filename:
                continue

            # Sanitize filename to prevent path traversal
            safe_filename = re.sub(r"[^a-zA-Z0-9._-]", "_", os.path.basename(filename))
            # Strip leading dots to prevent hidden files or .. sequences
            safe_filename = safe_filename.lstrip(".")
            if not safe_filename:
                safe_filename = f"attachment_{secrets.token_hex(4)}"

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            if len(payload) > max_bytes:
                logger.warning(
                    "Skipping attachment %s (%d bytes > %d limit)",
                    filename, len(payload), max_bytes,
                )
                continue

            attach_dir.mkdir(parents=True, exist_ok=True)
            filepath = attach_dir / safe_filename
            await asyncio.to_thread(filepath.write_bytes, payload)

            attachments.append({
                "filename": safe_filename,
                "path": str(filepath),
                "size_bytes": len(payload),
                "content_type": part.get_content_type(),
            })

        return attachments

    # ── Body Extraction ──────────────────────────────────────────────────

    def _extract_body(
        self, msg: email_lib.message.Message,
    ) -> tuple[str | None, str | None]:
        """Extract plain text and HTML body from an email."""
        body_text = None
        body_html = None

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" and not body_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                elif content_type == "text/html" and not body_html:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_html = payload.decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
                if content_type == "text/html":
                    body_html = decoded
                else:
                    body_text = decoded

        return body_text, body_html

    # ── Project Resolution ───────────────────────────────────────────────

    async def _resolve_project(self, tags: list[str]) -> str | None:
        """Resolve a project ID from subject tags."""
        if not tags:
            return self.config.default_project

        from ..db.repositories import resolve_project_id

        # Try each tag as a project name
        for tag in tags:
            try:
                project_id = resolve_project_id(tag)
                if project_id:
                    return project_id
            except Exception:
                continue

        return self.config.default_project

    # ── Thread Maintenance ───────────────────────────────────────────────

    async def close_stale_threads(self):
        """Close threads that have been idle beyond auto_close_hours."""
        stale = self._repo.get_stale_threads(self.config.auto_close_hours)
        for thread in stale:
            self._repo.update_thread_status(thread.id, EmailThreadStatus.CLOSED)
            logger.info("Auto-closed stale email thread %s", thread.id)
        return len(stale)

    # ── Status ───────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return channel status for API."""
        threads = self._repo.list_threads(limit=5)
        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "imap_host": self.config.imap_host,
            "poll_interval": self.config.poll_interval,
            "recent_threads": [
                {
                    "id": t.id,
                    "subject": t.subject,
                    "status": t.status.value,
                    "from": t.from_address,
                    "message_count": t.message_count,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in threads
            ],
        }
