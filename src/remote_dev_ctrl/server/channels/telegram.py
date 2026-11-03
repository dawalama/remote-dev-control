"""Telegram bot integration for RDC Command Center.

Modernized for goal-centered sessions, workstreams, and review-and-accept workflows.
"""

import asyncio
import logging
from typing import Callable, Awaitable, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Theme-aware status indicators for Telegram messages
TELEGRAM_THEMES = {
    'modern': {'running': '●', 'stopped': '○', 'failed': '✕', 'pending': '◐', 'header': '—'},
    'default': {'running': '🟢', 'stopped': '⬛', 'failed': '🔴', 'pending': '⏳', 'header': '━'},
    'brutalist': {'running': '▶', 'stopped': '■', 'failed': '✖', 'pending': '▷', 'header': '═'},
}


class TelegramBot:
    """Telegram bot for remote control of RDC."""
    
    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        on_command: Callable[[str, str, int], Awaitable[Any]] | None = None,
        dashboard_url: str | None = None,
    ):
        self.token = token
        self.allowed_users = set(allowed_users) if allowed_users else None
        self.on_command = on_command
        self.dashboard_url = dashboard_url
        self._bot = None
        self._app = None
        self._running = False
        self._user_projects: dict[int, str] = {}  # user_id -> selected project
        self._user_state: dict[int, dict[str, Any]] = {}  # user_id -> conversational state
    
    async def start(self):
        """Start the Telegram bot."""
        try:
            from telegram import BotCommand
            from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
        except ImportError:
            logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return False
        
        self._app = Application.builder().token(self.token).build()
        self._bot = self._app.bot
        
        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("brief", self._handle_brief))
        self._app.add_handler(CommandHandler("homebase", self._handle_brief))
        self._app.add_handler(CommandHandler("sessions", self._handle_sessions))
        self._app.add_handler(CommandHandler("reviews", self._handle_reviews))
        self._app.add_handler(CommandHandler("goal", self._handle_goal))
        self._app.add_handler(CommandHandler("accept", self._handle_accept))
        self._app.add_handler(CommandHandler("proof", self._handle_proof))
        self._app.add_handler(CommandHandler("instruct", self._handle_instruct))
        self._app.add_handler(CommandHandler("approvals", self._handle_approvals))
        self._app.add_handler(CommandHandler("approve", self._handle_approve))
        self._app.add_handler(CommandHandler("reject", self._handle_reject))
        self._app.add_handler(CommandHandler("workstreams", self._handle_workstreams))
        self._app.add_handler(CommandHandler("channels", self._handle_workstreams))
        self._app.add_handler(CommandHandler("projects", self._handle_projects))
        self._app.add_handler(CommandHandler("project", self._handle_select_project))
        self._app.add_handler(CommandHandler("processes", self._handle_processes))
        self._app.add_handler(CommandHandler("logs", self._handle_logs))
        self._app.add_handler(CommandHandler("cancel", self._handle_cancel))
        self._app.add_handler(CommandHandler("dashboard", self._handle_dashboard))
        
        # Backwards compatibility handlers
        self._app.add_handler(CommandHandler("tasks", self._handle_sessions))
        self._app.add_handler(CommandHandler("add", self._handle_goal))
        self._app.add_handler(CommandHandler("run", self._handle_sessions))

        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        
        # Set bot commands for autocomplete
        commands = [
            BotCommand("status", "📊 System overview"),
            BotCommand("brief", "🧭 HomeBase control-plane brief"),
            BotCommand("sessions", "🎯 Active & recent goal sessions"),
            BotCommand("reviews", "🔍 Sessions awaiting review"),
            BotCommand("goal", "⚡ New goal: /goal [proj] <text>"),
            BotCommand("accept", "✅ Accept review outcome: /accept <id>"),
            BotCommand("approvals", "⚠️ Pending command approvals"),
            BotCommand("workstreams", "🌊 List active workstreams"),
            BotCommand("project", "📂 Select active project: /project <name>"),
            BotCommand("projects", "📁 List all projects"),
            BotCommand("processes", "⚙️ Background services & ports"),
            BotCommand("logs", "📜 View logs"),
            BotCommand("cancel", "🛑 Cancel session: /cancel <id>"),
            BotCommand("help", "❓ Show all commands"),
        ]
        
        await self._app.initialize()
        await self._bot.set_my_commands(commands)
        await self._app.start()
        await self._app.updater.start_polling()
        
        self._running = True
        me = await self._bot.get_me()
        logger.info(f"Telegram bot started: @{me.username}")
        return True
    
    async def stop(self):
        """Stop the Telegram bot gracefully."""
        if not self._app or not self._running:
            return
        self._running = False
        try:
            if self._app.updater and getattr(self._app.updater, "running", True):
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")
        except Exception as e:
            logger.warning("Telegram stop: %s", e)
    
    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        if self.allowed_users is None:
            return True
        return user_id in self.allowed_users
    
    async def _unauthorized(self, update):
        """Send unauthorized message."""
        await update.message.reply_text(
            "⛔ Unauthorized\n\n"
            f"Your user ID: `{update.effective_user.id}`\n\n"
            "Add this ID to allowed_users in config.",
            parse_mode="Markdown"
        )
    
    def _get_user_project(self, user_id: int) -> str | None:
        """Get the selected project for a user."""
        return self._user_projects.get(user_id)
    
    def _set_user_project(self, user_id: int, project: str):
        """Set the selected project for a user."""
        self._user_projects[user_id] = project
    
    def _project_context(self, user_id: int) -> str:
        """Get project context string for display."""
        project = self._get_user_project(user_id)
        if project:
            return f"📂 `{project}`"
        return "📂 _No project selected_"
    
    def _make_keyboard(self, buttons: list[list[tuple[str, str]]]):
        """Create an inline keyboard from button definitions.
        
        buttons: [[("Label", "callback_data"), ...], ...]
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton(text, callback_data=data) for text, data in row]
            for row in buttons
        ]
        return InlineKeyboardMarkup(keyboard)
    
    async def _send_or_edit(self, target, result: Any, default_buttons=None):
        """Send a new message or edit an existing message from callback query."""
        reply_markup = None
        if isinstance(result, dict):
            text = result.get("text", "")
            buttons = result.get("buttons")
            if buttons is not None:
                reply_markup = self._make_keyboard(buttons)
        else:
            text = str(result)
        
        if reply_markup is None and default_buttons:
            reply_markup = self._make_keyboard(default_buttons)
            
        if hasattr(target, "edit_message_text"):
            try:
                await target.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.warning(f"edit_message_text failed: {e}")
        elif hasattr(target, "reply_text"):
            await target.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    
    async def _show_main_menu(self, query, user_id: int):
        """Show the main menu with quick actions."""
        current_project = self._get_user_project(user_id)
        
        buttons = [
            [("📊 Status", "refresh_status"), ("🧭 Brief Me", "show_brief")],
            [("🎯 Sessions", "show_sessions"), ("🔍 Reviews", "show_reviews")],
            [("⚡ New Goal", f"quick_goal:{current_project}" if current_project else "show_projects"), ("🌊 Workstreams", "show_workstreams")],
            [("📁 Projects", "show_projects"), ("⚙️ Services", "show_processes")],
        ]
        
        if current_project:
            buttons.append([
                (f"📜 Logs ({current_project})", f"logs_project:{current_project}"),
            ])
        
        keyboard = self._make_keyboard(buttons)
        await query.edit_message_text(
            f"{self._project_context(user_id)}\n\n*RDC Command Center*\n\nSelect an action:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    async def _handle_callback(self, update, context):
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()
        
        if not self._is_authorized(query.from_user.id):
            await query.edit_message_text("⛔ Unauthorized")
            return
        
        user_id = query.from_user.id
        data = query.data
        
        # Parse callback data: "action:param"
        if ":" in data:
            action, param = data.split(":", 1)
        else:
            action, param = data, ""
        
        if action == "main_menu":
            await self._show_main_menu(query, user_id)
            return

        if not self.on_command:
            await query.edit_message_text("❌ No command handler configured.")
            return

        if action == "refresh_status":
            result = await self.on_command("status", "", user_id)
            await self._send_or_edit(query, result)
        
        elif action == "show_brief":
            result = await self.on_command("brief", "", user_id)
            await self._send_or_edit(query, result)
        
        elif action == "show_sessions":
            result = await self.on_command("sessions", "", user_id)
            await self._send_or_edit(query, result)
        
        elif action == "show_reviews":
            result = await self.on_command("reviews", "", user_id)
            await self._send_or_edit(query, result)
            
        elif action == "show_approvals":
            result = await self.on_command("approvals", "", user_id)
            await self._send_or_edit(query, result)

        elif action == "show_workstreams":
            result = await self.on_command("workstreams", "", user_id)
            await self._send_or_edit(query, result)

        elif action == "accept_session":
            result = await self.on_command("accept_session", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "proof_session":
            result = await self.on_command("proof_session", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "instruct_prompt":
            self._user_state[user_id] = {"action": "instruct", "session_id": param}
            buttons = [[("⬅️ Cancel", "show_sessions")]]
            await query.edit_message_text(
                f"💬 *Send Instruction to Session* `{param}`\n\n"
                "Type what the agent should do next (or send a voice note):",
                parse_mode="Markdown",
                reply_markup=self._make_keyboard(buttons)
            )

        elif action == "quick_goal":
            self._user_state[user_id] = {"action": "goal", "project": param}
            buttons = [[("⬅️ Cancel", "show_projects")]]
            await query.edit_message_text(
                f"⚡ *New Goal for* `{param}`\n\n"
                "Type your goal description (or send a voice note):",
                parse_mode="Markdown",
                reply_markup=self._make_keyboard(buttons)
            )

        elif action == "approve_cmd":
            result = await self.on_command("decide_approval", f"{param} approved", user_id)
            await self._send_or_edit(query, result)

        elif action == "reject_cmd":
            result = await self.on_command("decide_approval", f"{param} rejected", user_id)
            await self._send_or_edit(query, result)

        elif action == "cancel_session":
            result = await self.on_command("cancel_session", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "retry_session":
            result = await self.on_command("retry_session", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "select_project":
            self._set_user_project(user_id, param)
            buttons = [
                [("⚡ Start Goal", f"quick_goal:{param}"), ("📜 Logs", f"logs_project:{param}")],
                [("🎯 Sessions", "show_sessions"), ("⬅️ Back to Menu", "main_menu")],
            ]
            await query.edit_message_text(
                f"✅ *Active Project Selected*\n\n📂 `{param}`\n\nCommands like /goal and /logs will now default to this project.",
                parse_mode="Markdown",
                reply_markup=self._make_keyboard(buttons)
            )

        elif action == "show_projects":
            result = await self.on_command("projects", "", user_id)
            await self._send_or_edit(query, result)

        elif action == "show_processes":
            result = await self.on_command("processes", "", user_id)
            await self._send_or_edit(query, result)

        elif action == "start_action":
            result = await self.on_command("start_action", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "stop_action":
            result = await self.on_command("stop_action", param, user_id)
            await self._send_or_edit(query, result)

        elif action == "logs_project":
            result = await self.on_command("logs", f"{param} 20", user_id)
            buttons = [[("⬅️ Back", "main_menu")]]
            await query.edit_message_text(
                f"📜 *Logs:* `{param}`\n\n```\n{result}\n```",
                parse_mode="Markdown",
                reply_markup=self._make_keyboard(buttons)
            )
    
    async def _handle_start(self, update, context):
        """Handle /start command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        current_project = self._get_user_project(user_id)
        
        buttons = [
            [("📊 Status", "refresh_status"), ("🧭 Brief Me", "show_brief")],
            [("🎯 Sessions", "show_sessions"), ("🔍 Reviews", "show_reviews")],
            [("⚡ New Goal", f"quick_goal:{current_project}" if current_project else "show_projects"), ("🌊 Workstreams", "show_workstreams")],
            [("📁 Select Project", "show_projects"), ("⚙️ Services", "show_processes")],
        ]
        
        keyboard = self._make_keyboard(buttons)
        await update.message.reply_text(
            f"{self._project_context(user_id)}\n\n"
            "👋 *Welcome to RDC Command Center*\n\n"
            "Remote control for your AI agent workstreams, goal sessions, and outcome reviews.\n\n"
            "Tap an action below, send a voice note, or type /help for all commands.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    async def _handle_help(self, update, context):
        """Handle /help command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        project_context = self._project_context(user_id)
        
        help_text = (
            f"📚 *RDC Command Center Guide*\n\n"
            f"{project_context}\n\n"
            "*🎯 Goals & Sessions:*\n"
            "• /goal `<proj> <text>` - Launch a goal session\n"
            "• /sessions - View active and recent sessions\n"
            "• /cancel `<id>` - Cancel an active session\n"
            "• /instruct `<id> <text>` - Send instruction to agent\n\n"
            "*🔍 Reviews & Approvals:*\n"
            "• /reviews - View sessions awaiting review\n"
            "• /accept `<id>` - Accept outcome and complete session\n"
            "• /proof `<id>` - View verification proof artifact\n"
            "• /approvals - Pending terminal command approvals\n"
            "• /approve `<id>` / /reject `<id>` - Decide approval\n\n"
            "*🧭 Overview & Monitoring:*\n"
            "• /brief - HomeBase control-plane status brief\n"
            "• /status - System overview and quick actions\n"
            "• /workstreams - Active channels and session counts\n"
            "• /processes - Running services and ports\n"
            "• /logs `[project]` - Recent agent logs\n\n"
            "*📁 Project Context:*\n"
            "• /project `<name>` - Switch active project\n"
            "• /projects - List registered projects\n\n"
            "_💡 Tip: You can send voice notes anytime to issue goals or ask for a brief!_"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def _handle_status(self, update, context):
        """Handle /status command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("status", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_brief(self, update, context):
        """Handle /brief command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("brief", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_sessions(self, update, context):
        """Handle /sessions command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("sessions", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_reviews(self, update, context):
        """Handle /reviews command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("reviews", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_goal(self, update, context):
        """Handle /goal command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        selected_project = self._get_user_project(user_id)
        
        if not context.args:
            if selected_project:
                self._user_state[user_id] = {"action": "goal", "project": selected_project}
                buttons = [[("⬅️ Cancel", "main_menu")]]
                await update.message.reply_text(
                    f"⚡ *New Goal for* `{selected_project}`\n\n"
                    "Type what you want the agent to accomplish (or send a voice note):",
                    parse_mode="Markdown",
                    reply_markup=self._make_keyboard(buttons)
                )
            else:
                if self.on_command:
                    projects_result = await self.on_command("projects", "", user_id)
                    await self._send_or_edit(update.message, projects_result)
            return
        
        first_arg = context.args[0]
        if selected_project and len(context.args) >= 1:
            project = selected_project
            goal_text = " ".join(context.args)
        elif len(context.args) >= 2:
            project = first_arg
            goal_text = " ".join(context.args[1:])
        else:
            await update.message.reply_text(
                "*Usage:* `/goal <description>` (with project selected)\n"
                "or `/goal <project> <description>`",
                parse_mode="Markdown"
            )
            return
        
        if self.on_command:
            result = await self.on_command("goal", f"{project} {goal_text}", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_accept(self, update, context):
        """Handle /accept command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            # Show reviews list so user can tap accept button
            if self.on_command:
                result = await self.on_command("reviews", "", user_id)
                await self._send_or_edit(update.message, result)
            return
        
        session_id = context.args[0]
        if self.on_command:
            result = await self.on_command("accept_session", session_id, user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_proof(self, update, context):
        """Handle /proof command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("Usage: `/proof <session_id>`", parse_mode="Markdown")
            return
        
        session_id = context.args[0]
        if self.on_command:
            result = await self.on_command("proof_session", session_id, user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_instruct(self, update, context):
        """Handle /instruct command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("Usage: `/instruct <session_id> <instruction text>`", parse_mode="Markdown")
            return
        
        if len(context.args) == 1:
            session_id = context.args[0]
            self._user_state[user_id] = {"action": "instruct", "session_id": session_id}
            await update.message.reply_text(
                f"💬 *Instruct Session* `{session_id}`\n\nType your instruction below:",
                parse_mode="Markdown"
            )
            return
        
        session_id = context.args[0]
        instruction = " ".join(context.args[1:])
        if self.on_command:
            result = await self.on_command("instruct_session", f"{session_id} {instruction}", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_approvals(self, update, context):
        """Handle /approvals command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("approvals", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_approve(self, update, context):
        """Handle /approve command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("Usage: `/approve <approval_id>`", parse_mode="Markdown")
            return
        
        approval_id = context.args[0]
        if self.on_command:
            result = await self.on_command("decide_approval", f"{approval_id} approved", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_reject(self, update, context):
        """Handle /reject command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("Usage: `/reject <approval_id>`", parse_mode="Markdown")
            return
        
        approval_id = context.args[0]
        if self.on_command:
            result = await self.on_command("decide_approval", f"{approval_id} rejected", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_workstreams(self, update, context):
        """Handle /workstreams command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("workstreams", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_projects(self, update, context):
        """Handle /projects command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("projects", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_select_project(self, update, context):
        """Handle /project command to select active project."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            current = self._get_user_project(user_id)
            if self.on_command:
                projects_result = await self.on_command("projects", "", user_id)
                prefix = f"📂 *Current Project:* `{current}`\n\n" if current else "📂 *No project selected*\n\n"
                await update.message.reply_text(f"{prefix}Use `/project <name>` to select.\n\n", parse_mode="Markdown")
                await self._send_or_edit(update.message, projects_result)
            return
        
        project = context.args[0]
        self._set_user_project(user_id, project)
        buttons = [
            [("⚡ Start Goal", f"quick_goal:{project}"), ("📜 Logs", f"logs_project:{project}")],
            [("🎯 Sessions", "show_sessions"), ("⬅️ Main Menu", "main_menu")],
        ]
        await update.message.reply_text(
            f"✅ *Project Selected*\n\n📂 `{project}`\n\nCommands like /goal and /logs will now default to this project.",
            parse_mode="Markdown",
            reply_markup=self._make_keyboard(buttons)
        )

    async def _handle_processes(self, update, context):
        """Handle /processes command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if self.on_command:
            result = await self.on_command("processes", "", user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_logs(self, update, context):
        """Handle /logs command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        project = context.args[0] if context.args else self._get_user_project(user_id)
        lines = context.args[1] if len(context.args) > 1 else "20"
        
        if not project:
            if self.on_command:
                result = await self.on_command("projects", "", user_id)
                await update.message.reply_text(
                    "*Usage:* `/logs [project] [lines]`\n\n_Select a project first with /project or specify one._\n\n",
                    parse_mode="Markdown"
                )
                await self._send_or_edit(update.message, result)
            return
        
        if self.on_command:
            result = await self.on_command("logs", f"{project} {lines}", user_id)
            await update.message.reply_text(f"📜 *Logs:* `{project}`\n\n```\n{result}\n```", parse_mode="Markdown")

    async def _handle_cancel(self, update, context):
        """Handle /cancel command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        if not context.args:
            # Show active sessions to cancel
            if self.on_command:
                result = await self.on_command("sessions", "", user_id)
                await self._send_or_edit(update.message, result)
            return
        
        session_id = context.args[0]
        if self.on_command:
            result = await self.on_command("cancel_session", session_id, user_id)
            await self._send_or_edit(update.message, result)

    async def _handle_dashboard(self, update, context):
        """Handle /dashboard command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        if self.dashboard_url:
            await update.message.reply_text(f"🔗 *Dashboard*\n\n{self.dashboard_url}", parse_mode="Markdown")
        else:
            await update.message.reply_text("Dashboard URL not configured.\nSet `dashboard_url` in config.", parse_mode="Markdown")

    async def _handle_message(self, update, context):
        """Handle regular text messages with conversational state and NLP matching."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if not self.on_command:
            return

        # Check conversational state first
        state = self._user_state.pop(user_id, None)
        if state:
            action = state.get("action")
            if action == "goal":
                project = state.get("project")
                result = await self.on_command("goal", f"{project} {text}", user_id)
                await self._send_or_edit(update.message, result)
                return
            elif action == "instruct":
                session_id = state.get("session_id")
                result = await self.on_command("instruct_session", f"{session_id} {text}", user_id)
                await self._send_or_edit(update.message, result)
                return

        # Pass to NLP router
        result = await self.on_command("message", text, user_id)
        await self._send_or_edit(update.message, result)

    async def _handle_voice(self, update, context):
        """Handle voice messages — transcribe and process with state and NLP."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        voice = update.message.voice or update.message.audio
        if not voice:
            await update.message.reply_text("❌ Could not process audio")
            return
        
        processing_msg = await update.message.reply_text("🎤 Processing voice message...")
        
        try:
            file = await voice.get_file()
            audio_bytes = await file.download_as_bytearray()
            transcript = await self._transcribe_audio(bytes(audio_bytes))
            
            if not transcript:
                await processing_msg.edit_text("❌ Could not transcribe audio. Please try again or type your message.")
                return
            
            await processing_msg.edit_text(f"🎤 _{transcript}_\n\n⏳ Processing...")
            
            if self.on_command:
                # Check conversational state first
                state = self._user_state.pop(user_id, None)
                if state:
                    action = state.get("action")
                    if action == "goal":
                        project = state.get("project")
                        result = await self.on_command("goal", f"{project} {transcript}", user_id)
                        await self._send_or_edit(processing_msg, result)
                        return
                    elif action == "instruct":
                        session_id = state.get("session_id")
                        result = await self.on_command("instruct_session", f"{session_id} {transcript}", user_id)
                        await self._send_or_edit(processing_msg, result)
                        return

                result = await self.on_command("message", transcript, user_id)
                await self._send_or_edit(processing_msg, result)
        
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            await processing_msg.edit_text(f"❌ Error processing voice: `{e}`", parse_mode="Markdown")

    async def _transcribe_audio(self, audio_bytes: bytes) -> str | None:
        """Transcribe audio bytes to text using Deepgram or Whisper."""
        import httpx
        import os
        from ..vault import get_secret
        
        deepgram_key = get_secret("DEEPGRAM_API_KEY") or os.environ.get("DEEPGRAM_API_KEY")
        if deepgram_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.deepgram.com/v1/listen",
                        headers={
                            "Authorization": f"Token {deepgram_key}",
                            "Content-Type": "audio/ogg",
                        },
                        content=audio_bytes,
                        params={"model": "nova-2", "smart_format": "true"},
                        timeout=30.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        transcript = data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
                        return transcript.strip() if transcript else None
            except Exception as e:
                logger.error(f"Deepgram transcription error: {e}")
        
        openai_key = get_secret("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if openai_key:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                        data={"model": "whisper-1"},
                        timeout=30.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        return data.get("text", "").strip() or None
            except Exception as e:
                logger.error(f"Whisper transcription error: {e}")
        
        logger.warning("No STT service available (set DEEPGRAM_API_KEY or OPENAI_API_KEY)")
        return None

    async def send_message(self, user_id: int, text: str, parse_mode: str = "Markdown", reply_markup=None):
        """Send a message to a user."""
        if self._bot:
            await self._bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)

    async def broadcast(self, text: str):
        """Send a message to all allowed users."""
        if self._bot and self.allowed_users:
            for user_id in self.allowed_users:
                try:
                    await self.send_message(user_id, text)
                except Exception as e:
                    logger.error(f"Failed to send to {user_id}: {e}")

    async def broadcast_session_event(
        self,
        text: str,
        session_id: str,
        event_type: str,
        status: str,
    ):
        """Send an interactive session notification card with inline buttons to all allowed users."""
        if not self._bot or not self.allowed_users:
            return
        
        buttons = []
        if status in ("verifying", "ready_for_review"):
            buttons = [
                [
                    ("✅ Accept & Complete", f"accept_session:{session_id}"),
                    ("📋 View Proof", f"proof_session:{session_id}"),
                ],
                [
                    ("💬 Instruct", f"instruct_prompt:{session_id}"),
                    ("🛑 Stop", f"cancel_session:{session_id}"),
                ],
            ]
        elif status in ("waiting", "needs_you"):
            buttons = [
                [
                    ("💬 Send Input", f"instruct_prompt:{session_id}"),
                    ("🛑 Cancel", f"cancel_session:{session_id}"),
                ]
            ]
        elif status == "failed":
            buttons = [
                [
                    ("🔄 Retry", f"retry_session:{session_id}"),
                    ("📜 Logs", f"logs_project:{session_id}"),
                ]
            ]
        elif status in ("running", "working"):
            buttons = [
                [
                    ("💬 Instruct", f"instruct_prompt:{session_id}"),
                    ("🛑 Cancel", f"cancel_session:{session_id}"),
                ]
            ]
        
        keyboard = self._make_keyboard(buttons) if buttons else None
        
        for user_id in self.allowed_users:
            try:
                await self._bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.error(f"Failed to send session notification to {user_id}: {e}")
