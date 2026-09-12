"""Telegram handlers for authorized text and voice task requests."""

from __future__ import annotations

import logging
from io import BytesIO

from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from agents.base import LLMAgentError
from agents.orchestrator import OrchestrationError, Orchestrator, ChatResponse
from config.settings import Settings
from services.interaction_service import InteractionBroker, InteractionTimeout
from services.memory_service import MemoryService
from utils.audio import SpeechSynthesizer, VoiceTranscriber

logger = logging.getLogger(__name__)


class TelegramHandler:
    """Authenticate users, report progress in-place, and route tasks."""

    def __init__(
        self,
        settings: Settings,
        orchestrator: Orchestrator,
        voice_transcriber: VoiceTranscriber,
        *,
        memory: MemoryService | None = None,
    ) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.voice_transcriber = voice_transcriber
        self.memory = memory
        self.interactions = InteractionBroker(settings.question_timeout_seconds)

    def is_authorized(self, update: Update) -> bool:
        """Return whether the update comes from the configured Telegram whitelist."""
        user = update.effective_user
        return user is not None and user.id in self.settings.telegram_allowed_users

    async def _reject_unauthorized(self, update: Update) -> None:
        """Reject without revealing configuration details."""
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This bot is private.")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start."""
        del context
        if not self.is_authorized(update):
            await self._reject_unauthorized(update)
            return
        user = update.effective_user
        if user is not None:
            await self.interactions.cancel(user.id)
        await update.effective_message.reply_text(
            "⚡ Arjun is ready. Send a coding request as text, a voice note, or a .txt file."
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /cancel to abort any waiting question."""
        del context
        if not self.is_authorized(update):
            await self._reject_unauthorized(update)
            return
        user = update.effective_user
        if user is not None and await self.interactions.cancel(user.id):
            await update.effective_message.reply_text("🛑 Cancelled the pending question.")
        else:
            await update.effective_message.reply_text("ℹ️ No active question to cancel.")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle a free-form text developer request."""
        del context
        if not self.is_authorized(update):
            await self._reject_unauthorized(update)
            return
        request = (update.effective_message.text or "").strip()
        if await self._submit_pending_answer(update, request):
            return
        # Detect "continue / resume / retry" so the user can restart a failed task.
        if request.casefold() in {
            "continue", "resume", "retry", "continue please",
            "try again", "redo", "restart", "go", "go on",
        }:
            resumed = await self._resume_last_task(update)
            if resumed:
                return
            # No failed task found — fall through and treat as normal request.
        await self._run_task(update, request)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Download a voice note into memory and send its bytes to Gemini."""
        if not self.is_authorized(update):
            await self._reject_unauthorized(update)
            return
        message = update.effective_message
        if message is None or message.voice is None:
            return
        status = await message.reply_text("⚡ Receiving voice instruction...")
        try:
            await context.bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.RECORD_VOICE,
            )
            voice_file = await message.voice.get_file()
            audio_bytes = bytes(await voice_file.download_as_bytearray())
            request = await self.voice_transcriber.transcribe(audio_bytes)
            if request is None:
                await status.edit_text("🔇 I could not detect a clear developer instruction in that voice note.")
                return
            if await self._submit_pending_answer(update, request):
                await status.edit_text("✅ Answer received. Continuing the build...")
                return
            await self._run_task(update, request, status_message=status, voice_reply=True)
        except ChatResponse as response:
            await status.edit_text(str(response))
        except (LLMAgentError, OrchestrationError, InteractionTimeout) as error:
            logger.exception("Voice task failed")
            await status.edit_text(f"❌ Task failed: {error}")
        except Exception:
            logger.exception("Unexpected voice handler failure")
            await status.edit_text("❌ I could not process that voice note. Please try again.")

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle .txt file uploads — read the file and treat its content as a coding request."""
        if not self.is_authorized(update):
            await self._reject_unauthorized(update)
            return
        message = update.effective_message
        if message is None or message.document is None:
            return
        doc = message.document
        mime = doc.mime_type or ""
        # Only handle plain-text files
        if mime not in {"text/plain", "application/octet-stream"} and not (
            doc.file_name or ""
        ).endswith(".txt"):
            await message.reply_text(
                "⚠️ I can only read .txt files. Please send your request as text or a .txt file."
            )
            return
        status = await message.reply_text("📄 Reading your file...")
        try:
            file = await doc.get_file()
            raw = await file.download_as_bytearray()
            file_text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            logger.exception("Could not download document")
            await status.edit_text("❌ I could not read the file. Please paste the text directly.")
            return
        # Combine caption (if any) with the file content
        caption = (message.caption or "").strip()
        if caption:
            request = f"{caption}\n\n{file_text}"
        else:
            request = file_text
        if not request.strip():
            await status.edit_text("❌ The file appears to be empty. Please send a non-empty request.")
            return
        await status.edit_text(f"📄 Got your file ({len(file_text)} chars). Starting build...")
        await self._run_task(update, request, status_message=status)

    async def _run_task(
        self,
        update: Update,
        request: str,
        *,
        status_message: Message | None = None,
        voice_reply: bool = False,
    ) -> None:
        """Run orchestration and edit one progress message through each phase."""
        message = update.effective_message
        if message is None:
            return
        status = status_message or await message.reply_text(
            "🏗️ Building your request...\n"
            "Flow: inspect repository → plan → code → review → GitHub → Vercel → auto-debug.\n"
            "Technology: the planner will detect the existing app stack and preserve it."
        )

        if voice_reply:
            await status.edit_text(
                "🏗️ Building your request...\n"
                "I’ll explain the plan and technology, then code, review, commit, deploy, and auto-debug."
            )
            voice_ack = await SpeechSynthesizer.synthesize(
                "Building your request now. I will inspect your repository, explain the plan "
                "and technology, generate and review the code, commit it to GitHub, deploy it "
                "to Vercel, and automatically debug any build errors."
            )
            if voice_ack:
                try:
                    await message.reply_audio(
                        audio=BytesIO(voice_ack),
                        filename="arjun-building.mp3",
                        caption="🏗️ Building your request...",
                    )
                except Exception:
                    logger.debug("Could not send voice progress acknowledgement", exc_info=True)

        async def progress(text: str) -> None:
            try:
                await status.edit_text(text)
            except Exception:
                logger.debug("Could not edit Telegram progress message", exc_info=True)

        if not request.strip():
            await status.edit_text("❌ Please provide a non-empty coding request.")
            return
        try:
            user = update.effective_user
            result = await self.orchestrator.run(
                request,
                progress=progress,
                user_id=user.id if user is not None else None,
                ask_user=(
                    (lambda prompt, secret: self._ask_user(update, prompt, secret))
                    if user is not None
                    else None
                ),
            )
            github = result.github
            lines = [
                "✅ Task completed.",
                f"Commit: {github.commit_url}",
                f"Branch: {github.branch_url}",
            ]
            if result.deployment and result.deployment.ready:
                lines.extend(
                    [
                        "🌐 Production website:" if result.production else "🌐 Preview website:",
                        result.deployment.url,
                        f"Vercel deployment: {result.deployment.dashboard_url}",
                    ]
                )
            elif result.deployment:
                lines.append(f"⚠️ Vercel state: {result.deployment.state}")
            if github.file_urls:
                lines.append("Files:\n" + "\n".join(f"• {url}" for url in github.file_urls))
            await status.edit_text("\n".join(lines))
        except ChatResponse as response:
            await status.edit_text(str(response))
        except (LLMAgentError, OrchestrationError, InteractionTimeout, ValueError) as error:
            logger.exception("Task failed")
            await status.edit_text(f"❌ Task failed safely: {error}")
        except Exception:
            logger.exception("Unexpected task failure")
            await status.edit_text("❌ Unexpected failure. No completion was reported; check the worker logs.")

    async def _ask_user(self, update: Update, prompt: str, secret: bool) -> str:
        """Send a blocking question and wait for the user's next authorized message."""
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            raise OrchestrationError("Telegram user context is unavailable for a required question")
        await self.interactions.open(user.id, secret=secret)
        await message.reply_text(prompt)
        return await self.interactions.wait(user.id)

    async def _submit_pending_answer(self, update: Update, response: str) -> bool:
        """Route a message to a waiting task instead of accidentally starting a new task."""
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None or not response.strip():
            return False
        if not await self.interactions.is_pending(user.id):
            return False
        secret = await self.interactions.submit(user.id, response)
        try:
            if secret:
                # Best effort: remove the plaintext credential from the chat history.
                await message.delete()
                await message.reply_text("✅ Credential message received and encrypted. Continuing...")
            else:
                await message.reply_text("✅ Answer received. Continuing...")
        except Exception:
            logger.debug("Could not acknowledge or delete an interaction response", exc_info=True)
        return True

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log framework-level errors without leaking secrets to Telegram."""
        logger.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)

    async def _resume_last_task(self, update: Update) -> bool:
        """Look up and re-run the last failed or interrupted task. Returns True if found."""
        message = update.effective_message
        if message is None:
            return False
        if self.memory is None:
            await message.reply_text(
                "⚠️ No memory service is configured, so I cannot look up the last task.\n"
                "Please send your full request again."
            )
            return True
        last_request = await self.memory.get_last_failed_task()
        if not last_request:
            await message.reply_text(
                "ℹ️ I could not find a previous failed or interrupted task to resume.\n"
                "Please send your full request."
            )
            return True
        await message.reply_text(
            f"🔄 Resuming your last task:\n\n\"{last_request}\"\n\n"
            "Starting fresh from the beginning with the same request..."
        )
        await self._run_task(update, last_request)
        return True
