"""Telegram bot integration for RDC Command Center."""

import asyncio
import logging
from typing import Callable, Awaitable
from datetime import datetime

logger = logging.getLogger(__name__)

# Theme-aware status indicators for Telegram messages
TELEGRAM_THEMES = {
    'modern': {'running': '\u25cf', 'stopped': '\u25cb', 'failed': '\u2715', 'pending': '\u25d0', 'header': '\u2014'},
    'default': {'running': '\U0001f7e2', 'stopped': '\u2b1b', 'failed': '\U0001f534', 'pending': '\U0001f7e1', 'header': '\u2501'},
    'brutalist': {'running': '\u25b6', 'stopped': '\u25a0', 'failed': '\u2716', 'pending': '\u25b7', 'header': '\u2550'},
}


class TelegramBot:
    """Telegram bot for remote control of RDC."""
    
    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        on_command: Callable[[str, str, int], Awaitable[str]] | None = None,
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
    
    async def start(self):
        """Start the Telegram bot."""
        try:
            from telegram import Update, Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
            from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
        except ImportError:
            logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return False
        
        self._app = Application.builder().token(self.token).build()
        self._bot = self._app.bot
        
        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(CommandHandler("status", self._handle_status))

        self._app.add_handler(CommandHandler("tasks", self._handle_tasks))
        self._app.add_handler(CommandHandler("processes", self._handle_processes))

        self._app.add_handler(CommandHandler("add", self._handle_add_task))
        self._app.add_handler(CommandHandler("run", self._handle_run_task))
        self._app.add_handler(CommandHandler("cancel", self._handle_cancel_task))
        self._app.add_handler(CommandHandler("projects", self._handle_projects))
        self._app.add_handler(CommandHandler("project", self._handle_select_project))
        self._app.add_handler(CommandHandler("logs", self._handle_logs))
        self._app.add_handler(CommandHandler("dashboard", self._handle_dashboard))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._handle_voice))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
        
        # Set bot commands for autocomplete
        commands = [
            BotCommand("status", "📊 System overview"),
            BotCommand("project", "📂 Select project: /project <name>"),

            BotCommand("tasks", "📋 List pending tasks"),
            BotCommand("processes", "⚙️ List running processes"),
            BotCommand("projects", "📁 List all projects"),
            BotCommand("add", "➕ Add task: /add <description>"),
            BotCommand("run", "▶️ Run task: /run <task_id>"),
            BotCommand("cancel", "❌ Cancel task: /cancel <task_id>"),

            BotCommand("logs", "📜 View logs"),
            BotCommand("dashboard", "🔗 Get dashboard link"),
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
        self._running = False # Set to false immediately
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
    
    async def _show_main_menu(self, query, user_id: int):
        """Show the main menu with quick actions."""
        current_project = self._get_user_project(user_id)
        
        buttons = [
            [("📊 Status", "refresh_status")],
            [("📋 Tasks", "show_tasks"), ("⚙️ Processes", "show_processes")],
            [("📁 Projects", "show_projects")],
        ]
        
        if current_project:
            buttons.append([
                (f"📜 Logs", f"logs_project:{current_project}"),
            ])
        
        keyboard = self._make_keyboard(buttons)
        await query.edit_message_text(
            f"{self._project_context(user_id)}\n\n*Main Menu*\n\nSelect an action:",
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
        
        if action == "select_project":
            self._set_user_project(user_id, param)
            await query.edit_message_text(
                f"✅ *Project Selected*\n\n📂 `{param}`",
                parse_mode="Markdown"
            )
        

        
        elif action == "logs_project":
            if self.on_command:
                result = await self.on_command("logs", f"{param} 20", user_id)
                await query.edit_message_text(
                    f"📜 *Logs:* `{param}`\n\n```\n{result}\n```",
                    parse_mode="Markdown"
                )
        
        elif action == "run_task":
            if self.on_command:
                result = await self.on_command("run_task", param, user_id)
                await query.edit_message_text(result, parse_mode="Markdown")
        
        elif action == "cancel_task":
            if self.on_command:
                result = await self.on_command("cancel_task", param, user_id)
                await query.edit_message_text(result, parse_mode="Markdown")
        
        elif action == "start_process":
            if self.on_command:
                result = await self.on_command("start_process", param, user_id)
                await query.edit_message_text(result, parse_mode="Markdown")
        
        elif action == "stop_process":
            if self.on_command:
                result = await self.on_command("stop_process", param, user_id)
                await query.edit_message_text(result, parse_mode="Markdown")
        
        elif action == "refresh_status":
            if self.on_command:
                result = await self.on_command("status", "", user_id)
                keyboard = self._make_keyboard([[("🔄 Refresh", "refresh_status")]])
                await query.edit_message_text(
                    f"{self._project_context(user_id)}\n\n{result}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        
        elif action == "main_menu":
            await self._show_main_menu(query, user_id)
        
        elif action == "show_tasks":
            if self.on_command:
                result = await self.on_command("tasks", "", user_id)
                task_ids = self._parse_task_ids(result)
                
                buttons = []
                for task_id in task_ids[:5]:
                    short_id = task_id[:8]
                    buttons.append([
                        (f"▶️ Run {short_id}", f"run_task:{task_id}"),
                        (f"❌ Cancel {short_id}", f"cancel_task:{task_id}")
                    ])
                buttons.append([("⬅️ Back", "refresh_status")])
                
                keyboard = self._make_keyboard(buttons) if buttons else None
                await query.edit_message_text(result, parse_mode="Markdown", reply_markup=keyboard)
        
        elif action == "show_processes":
            if self.on_command:
                result = await self.on_command("processes", "", user_id)
                process_ids = self._parse_process_ids(result)
                
                buttons = []
                for proc_id, is_running in process_ids[:6]:
                    if is_running:
                        buttons.append([(f"🛑 Stop {proc_id}", f"stop_process:{proc_id}")])
                    else:
                        buttons.append([(f"▶️ Start {proc_id}", f"start_process:{proc_id}")])
                buttons.append([("⬅️ Back", "refresh_status")])
                
                keyboard = self._make_keyboard(buttons)
                await query.edit_message_text(result, parse_mode="Markdown", reply_markup=keyboard)
        
        elif action == "show_projects":
            if self.on_command:
                result = await self.on_command("projects", "", user_id)
                projects = self._parse_project_names(result)
                
                buttons = []
                for i in range(0, len(projects), 2):
                    row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                    buttons.append(row)
                buttons.append([("⬅️ Back", "refresh_status")])
                
                keyboard = self._make_keyboard(buttons)
                await query.edit_message_text(
                    f"{self._project_context(user_id)}\n\n*Select a project:*",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
    
    async def _handle_start(self, update, context):
        """Handle /start command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        
        buttons = [
            [("📊 Status", "refresh_status")],
            [("📋 Tasks", "show_tasks"), ("⚙️ Processes", "show_processes")],
            [("📁 Select Project", "show_projects")],
        ]
        
        keyboard = self._make_keyboard(buttons)
        await update.message.reply_text(
            "👋 *RDC Command Center*\n\n"
            "Control your tasks and projects remotely.\n\n"
            "Tap a button below or type /help for all commands.",
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
            f"📚 *RDC Commands*\n\n"
            f"{project_context}\n\n"
            "*Project Context:*\n"
            "• /project `<name>` - Select active project\n"
            "• /projects - List all projects\n\n"
            "*Status & Monitoring:*\n"
            "• /status - System overview\n"

            "• /tasks - List pending tasks\n"
            "• /processes - List running processes\n"
            "• /logs - View project logs\n\n"
            "*Task Management:*\n"
            "• /add `<description>` - Create task\n"
            "• /run `<task_id>` - Run a pending task\n"
            "• /cancel `<task_id>` - Cancel a task\n\n"

            "*Other:*\n"
            "• /dashboard - Get dashboard URL\n\n"
            "_Commands use selected project. Override with explicit project name._"
        )
        
        await update.message.reply_text(help_text, parse_mode="Markdown")
    
    async def _handle_status(self, update, context):
        """Handle /status command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        project_context = self._project_context(user_id)
        current_project = self._get_user_project(user_id)
        
        if self.on_command:
            result = await self.on_command("status", "", user_id)
            
            # Quick action buttons
            buttons = [
                [("📋 Tasks", "show_tasks"), ("⚙️ Processes", "show_processes")],
                [("📁 Projects", "show_projects"), ("🔄 Refresh", "refresh_status")],
            ]
            
            if current_project:
                buttons.insert(1, [
                    (f"📜 Logs", f"logs_project:{current_project}"),
                ])
            
            keyboard = self._make_keyboard(buttons)
            await update.message.reply_text(
                f"{project_context}\n\n{result}",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
    

    
    async def _handle_tasks(self, update, context):
        """Handle /tasks command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        
        if self.on_command:
            result = await self.on_command("tasks", "", user_id)
            task_ids = self._parse_task_ids(result)
            
            if task_ids:
                # Show tasks with action buttons
                buttons = []
                for task_id in task_ids[:5]:  # Limit to 5 tasks
                    short_id = task_id[:8]
                    buttons.append([
                        (f"▶️ Run {short_id}", f"run_task:{task_id}"),
                        (f"❌ Cancel {short_id}", f"cancel_task:{task_id}")
                    ])
                
                keyboard = self._make_keyboard(buttons)
                await update.message.reply_text(result, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await update.message.reply_text(result, parse_mode="Markdown")
    
    def _parse_task_ids(self, text: str) -> list[str]:
        """Extract task IDs from command output."""
        import re
        # Match UUID-like patterns or short IDs
        ids = re.findall(r'`([a-f0-9-]{8,36})`', text)
        return list(dict.fromkeys(ids))
    
    async def _handle_processes(self, update, context):
        """Handle /processes command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        
        if self.on_command:
            result = await self.on_command("processes", "", user_id)
            process_ids = self._parse_process_ids(result)
            
            if process_ids:
                buttons = []
                for proc_id, is_running in process_ids[:6]:  # Limit to 6
                    if is_running:
                        buttons.append([(f"🛑 Stop {proc_id}", f"stop_process:{proc_id}")])
                    else:
                        buttons.append([(f"▶️ Start {proc_id}", f"start_process:{proc_id}")])
                
                keyboard = self._make_keyboard(buttons)
                await update.message.reply_text(result, parse_mode="Markdown", reply_markup=keyboard)
            else:
                await update.message.reply_text(result, parse_mode="Markdown")
    
    def _parse_process_ids(self, text: str) -> list[tuple[str, bool]]:
        """Extract process IDs and their running state from command output."""
        import re
        processes = []
        for line in text.split("\n"):
            # Match patterns like "🟢 process-name" or "⚪ process-name"
            match = re.match(r'^[🟢⚪🔴]\s*`?([a-zA-Z0-9_-]+)`?', line.strip())
            if match:
                proc_id = match.group(1)
                is_running = "🟢" in line
                processes.append((proc_id, is_running))
        return processes
    
    async def _handle_projects(self, update, context):
        """Handle /projects command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        current = self._project_context(user_id)
        
        if self.on_command:
            result = await self.on_command("projects", "", user_id)
            projects = self._parse_project_names(result)
            
            if projects:
                # Create button grid (2 per row)
                buttons = []
                for i in range(0, len(projects), 2):
                    row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                    buttons.append(row)
                
                keyboard = self._make_keyboard(buttons)
                await update.message.reply_text(
                    f"{current}\n\n*Select a project:*",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                await update.message.reply_text(f"{current}\n\n{result}", parse_mode="Markdown")
    
    def _parse_project_names(self, text: str) -> list[str]:
        """Extract project names from command output."""
        import re
        # Match patterns like "• project_name" or "- project_name" or "`project_name`"
        projects = []
        for line in text.split("\n"):
            # Match "• name" or "- name" patterns
            match = re.match(r'^[•\-]\s*`?([a-zA-Z0-9_-]+)`?', line.strip())
            if match:
                projects.append(match.group(1))
            # Also match "`name`" patterns
            elif '`' in line:
                matches = re.findall(r'`([a-zA-Z0-9_-]+)`', line)
                projects.extend(matches)
        return list(dict.fromkeys(projects))  # Remove duplicates, preserve order
    
    async def _handle_select_project(self, update, context):
        """Handle /project command to select active project."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        
        if not context.args:
            # Show current project and list available
            current = self._get_user_project(user_id)
            if self.on_command:
                projects_list = await self.on_command("projects", "", user_id)
                if current:
                    await update.message.reply_text(
                        f"📂 *Current Project:* `{current}`\n\n"
                        f"Use `/project <name>` to switch.\n\n{projects_list}",
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text(
                        f"📂 *No project selected*\n\n"
                        f"Use `/project <name>` to select.\n\n{projects_list}",
                        parse_mode="Markdown"
                    )
            return
        
        project = context.args[0]
        self._set_user_project(user_id, project)
        
        await update.message.reply_text(
            f"✅ *Project Selected*\n\n"
            f"📂 `{project}`\n\n"
            f"Commands like /add and /logs will now use this project.",
            parse_mode="Markdown"
        )
    

    
    async def _handle_add_task(self, update, context):
        """Handle /add command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        selected_project = self._get_user_project(user_id)
        
        if not context.args:
            if self.on_command:
                result = await self.on_command("projects", "", user_id)
                if selected_project:
                    await update.message.reply_text(
                        f"*Usage:* `/add <description>`\n\n"
                        f"📂 Using project: `{selected_project}`\n\n"
                        f"_Or specify project: /add <project> <description>_",
                        parse_mode="Markdown"
                    )
                else:
                    await update.message.reply_text(
                        "*Usage:* `/add <project> <description>`\n\n"
                        "_Or select a project first with /project_\n\n" + result,
                        parse_mode="Markdown"
                    )
            return
        
        # Check if first arg is a project or part of description
        first_arg = context.args[0]
        
        if selected_project and len(context.args) >= 1:
            # Use selected project, all args are the task description
            project = selected_project
            task = " ".join(context.args)
        elif len(context.args) >= 2:
            # First arg is project, rest is description
            project = first_arg
            task = " ".join(context.args[1:])
        else:
            await update.message.reply_text(
                "*Usage:* `/add <description>` (with project selected)\n"
                "or `/add <project> <description>`",
                parse_mode="Markdown"
            )
            return
        
        if self.on_command:
            result = await self.on_command("add_task", f"{project} {task}", user_id)
            await update.message.reply_text(result, parse_mode="Markdown")
    
    async def _handle_run_task(self, update, context):
        """Handle /run command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        if not context.args:
            # Show pending tasks
            if self.on_command:
                result = await self.on_command("tasks", "", update.effective_user.id)
                await update.message.reply_text(
                    "*Usage:* `/run <task_id>`\n\n" + result,
                    parse_mode="Markdown"
                )
            return
        
        task_id = context.args[0]
        
        if self.on_command:
            result = await self.on_command("run_task", task_id, update.effective_user.id)
            await update.message.reply_text(result, parse_mode="Markdown")
    
    async def _handle_cancel_task(self, update, context):
        """Handle /cancel command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        if not context.args:
            if self.on_command:
                result = await self.on_command("tasks", "", update.effective_user.id)
                await update.message.reply_text(
                    "*Usage:* `/cancel <task_id>`\n\n" + result,
                    parse_mode="Markdown"
                )
            return
        
        task_id = context.args[0]
        
        if self.on_command:
            result = await self.on_command("cancel_task", task_id, update.effective_user.id)
            await update.message.reply_text(result, parse_mode="Markdown")
    
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
                    "*Usage:* `/logs [project] [lines]`\n\n"
                    "_Select a project first with /project or specify one._\n\n" + result,
                    parse_mode="Markdown"
                )
            return
        
        if self.on_command:
            result = await self.on_command("logs", f"{project} {lines}", user_id)
            await update.message.reply_text(f"📜 *Logs:* `{project}`\n\n```\n{result}\n```", parse_mode="Markdown")
    
    async def _handle_dashboard(self, update, context):
        """Handle /dashboard command."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        if self.dashboard_url:
            await update.message.reply_text(
                f"🔗 *Dashboard*\n\n{self.dashboard_url}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "Dashboard URL not configured.\n"
                "Set `dashboard_url` in the bot config.",
                parse_mode="Markdown"
            )
    
    async def _handle_message(self, update, context):
        """Handle regular text messages."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if self.on_command:
            result = await self.on_command("message", text, user_id)
            
            # Handle special return values
            keyboard = None
            if result.startswith("select_project:"):
                project = result.split(":", 1)[1]
                self._set_user_project(user_id, project)
                result = f"✅ *Project Selected*\n\n📂 `{project}`"
            elif result.startswith("select_project_notfound:"):
                attempted = result.split(":", 1)[1]
                projects_result = await self.on_command("projects", "", user_id)
                projects = self._parse_project_names(projects_result)
                if projects:
                    buttons = []
                    for i in range(0, len(projects), 2):
                        row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                        buttons.append(row)
                    keyboard = self._make_keyboard(buttons)
                result = f"🤔 Didn't find project `{attempted}`\n\n*Select one:*"
            elif result == "show_projects":
                projects_result = await self.on_command("projects", "", user_id)
                projects = self._parse_project_names(projects_result)
                if projects:
                    buttons = []
                    for i in range(0, len(projects), 2):
                        row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                        buttons.append(row)
                    keyboard = self._make_keyboard(buttons)
                result = f"*Select a project:*"
            
            await update.message.reply_text(result, parse_mode="Markdown", reply_markup=keyboard)
    
    async def _handle_voice(self, update, context):
        """Handle voice messages - transcribe and process as text."""
        if not self._is_authorized(update.effective_user.id):
            await self._unauthorized(update)
            return
        
        user_id = update.effective_user.id
        
        # Get the voice/audio file
        voice = update.message.voice or update.message.audio
        if not voice:
            await update.message.reply_text("❌ Could not process audio")
            return
        
        # Send "processing" indicator
        processing_msg = await update.message.reply_text("🎤 Processing voice message...")
        
        try:
            # Download the audio file
            file = await voice.get_file()
            audio_bytes = await file.download_as_bytearray()
            
            # Transcribe using our STT service
            transcript = await self._transcribe_audio(bytes(audio_bytes))
            
            if not transcript:
                await processing_msg.edit_text("❌ Could not transcribe audio. Please try again or type your message.")
                return
            
            # Show what was understood
            await processing_msg.edit_text(f"🎤 _{transcript}_\n\n⏳ Processing...")
            
            # Process as a regular message
            if self.on_command:
                result = await self.on_command("message", transcript, user_id)
                
                # Handle special return values
                keyboard = None
                if result.startswith("select_project:"):
                    project = result.split(":", 1)[1]
                    self._set_user_project(user_id, project)
                    result = f"✅ *Project Selected*\n\n📂 `{project}`"
                elif result.startswith("select_project_notfound:"):
                    attempted = result.split(":", 1)[1]
                    # Show project selection buttons
                    projects_result = await self.on_command("projects", "", user_id)
                    projects = self._parse_project_names(projects_result)
                    if projects:
                        buttons = []
                        for i in range(0, len(projects), 2):
                            row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                            buttons.append(row)
                        keyboard = self._make_keyboard(buttons)
                    result = f"🤔 Didn't find project `{attempted}`\n\n*Select one:*"
                elif result == "show_projects":
                    projects_result = await self.on_command("projects", "", user_id)
                    projects = self._parse_project_names(projects_result)
                    if projects:
                        buttons = []
                        for i in range(0, len(projects), 2):
                            row = [(f"📂 {p}", f"select_project:{p}") for p in projects[i:i+2]]
                            buttons.append(row)
                        keyboard = self._make_keyboard(buttons)
                    result = f"*Select a project:*"
                
                await processing_msg.edit_text(
                    f"🎤 _{transcript}_\n\n{result}", 
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            await processing_msg.edit_text(f"❌ Error processing voice: `{e}`", parse_mode="Markdown")
    
    async def _transcribe_audio(self, audio_bytes: bytes) -> str | None:
        """Transcribe audio bytes to text using Deepgram or Whisper."""
        import httpx
        import os
        from ..vault import get_secret
        
        # Try Deepgram first
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
                        params={
                            "model": "nova-2",
                            "smart_format": "true",
                        },
                        timeout=30.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        transcript = data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
                        return transcript.strip() if transcript else None
            except Exception as e:
                logger.error(f"Deepgram transcription error: {e}")
        
        # Fallback to OpenAI Whisper
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
    
    async def send_message(self, user_id: int, text: str, parse_mode: str = "Markdown"):
        """Send a message to a user."""
        if self._bot:
            await self._bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode)
    
    async def send_photo(self, user_id: int, photo, caption: str = None):
        """Send a photo to a user."""
        if self._bot:
            await self._bot.send_photo(chat_id=user_id, photo=photo, caption=caption, parse_mode="Markdown")
    
    async def broadcast(self, text: str):
        """Send a message to all allowed users."""
        if self._bot and self.allowed_users:
            for user_id in self.allowed_users:
                try:
                    await self.send_message(user_id, text)
                except Exception as e:
                    logger.error(f"Failed to send to {user_id}: {e}")
    
    async def notify_task_complete(self, task_id: str, project: str, result: str = None):
        """Notify users when a task completes."""
        msg = f"✅ *Task Completed*\n\nProject: `{project}`\nTask: `{task_id}`"
        if result:
            msg += f"\n\nResult:\n```\n{result[:500]}\n```"
        await self.broadcast(msg)
    
    async def notify_task_failed(self, task_id: str, project: str, error: str = None):
        """Notify users when a task fails."""
        msg = f"❌ *Task Failed*\n\nProject: `{project}`\nTask: `{task_id}`"
        if error:
            msg += f"\n\nError:\n```\n{error[:500]}\n```"
        await self.broadcast(msg)
    

