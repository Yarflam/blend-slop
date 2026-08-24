import bpy
import threading
import queue
from datetime import datetime
from bpy.types import Operator

from . import llm_client
from . import code_execution
from . import scene_context
from . import polyhaven
from .preferences import get_addon_preferences


LOG_TEXT_NAME = "AI Assistant Log"
ERROR_LOG_NAME = "AI Assistant Errors"

# Global queue for thread-safe communication between HTTP thread and main thread
_result_queue = queue.Queue()

# Request token: incremented on every new send and on stop, so stale thread
# results can be discarded. `_api_messages` is the in-flight API conversation.
_request_id = 0
_api_messages: list[dict[str, str]] = []


def _get_log_text() -> bpy.types.Text:
    """Get or create the Text block used for full conversation log."""
    if LOG_TEXT_NAME not in bpy.data.texts:
        bpy.data.texts.new(LOG_TEXT_NAME)
    return bpy.data.texts[LOG_TEXT_NAME]


def _log_write(role: str, content: str) -> None:
    """Append a message to the log Text block."""
    log = _get_log_text()
    prefix = {"user": "USER", "assistant": "AI", "system": "SYSTEM"}.get(role, role.upper())
    separator = "=" * 60
    log.write(f"\n{separator}\n[{prefix}]\n{separator}\n{content}\n")


def _get_error_log() -> bpy.types.Text:
    """Get or create the Text block for structured error collection."""
    if ERROR_LOG_NAME not in bpy.data.texts:
        bpy.data.texts.new(ERROR_LOG_NAME)
    return bpy.data.texts[ERROR_LOG_NAME]


def _get_provider_config(prefs) -> tuple[str | None, str, str]:
    """Return (api_key, model, base_url) for the selected provider.

    base_url is used by Anthropic-compatible providers and Ollama; empty for OpenAI.
    """
    provider = prefs.provider
    if provider == "CLAUDE":
        return prefs.claude_api_key, prefs.claude_model, llm_client.ANTHROPIC_BASE_URL
    if provider == "OPENAI":
        return prefs.openai_api_key, prefs.openai_model, ""
    if provider == "KIMI":
        base_url = prefs.kimi_base_url
        # kimi-for-coding/kimi-coding live on api.kimi.com, not api.kimi.ai
        if prefs.kimi_model != "k3" and base_url.rstrip("/") == "https://api.kimi.ai/coding":
            base_url = "https://api.kimi.com/coding"
        return prefs.kimi_api_key, prefs.kimi_model, base_url
    if provider == "DEEPSEEK":
        return prefs.deepseek_api_key, prefs.deepseek_model, prefs.deepseek_base_url
    return None, prefs.ollama_model, prefs.ollama_url


def _get_extra_headers(prefs) -> dict[str, str] | None:
    """Extra HTTP headers for Anthropic-compatible third-party providers."""
    if prefs.provider not in ("KIMI", "DEEPSEEK"):
        return None
    api_key = prefs.kimi_api_key if prefs.provider == "KIMI" else prefs.deepseek_api_key
    headers = {"Authorization": f"Bearer {api_key}"}
    if prefs.provider == "DEEPSEEK" and prefs.deepseek_long_context and prefs.deepseek_model == "deepseek-v4-pro":
        headers["anthropic-beta"] = "context-1m-2025-08-07"
    return headers


def _log_error(prompt: str, code: str, error: str) -> None:
    """Log a structured error entry for later analysis."""
    log = _get_error_log()
    prefs = get_addon_preferences()
    blender_ver = ".".join(str(v) for v in bpy.app.version)
    provider = prefs.provider
    _, model, _ = _get_provider_config(prefs)

    entry = (
        f"\n{'#' * 60}\n"
        f"## Error @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Blender: {blender_ver}\n"
        f"Provider: {provider} / {model}\n"
        f"\n### Prompt\n{prompt}\n"
        f"\n### Generated Code\n```python\n{code}\n```\n"
        f"\n### Traceback\n```\n{error}\n```\n"
    )
    log.write(entry)


def _redraw_views() -> None:
    """Force redraw of 3D viewports and Properties editors."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ("VIEW_3D", "PROPERTIES"):
                area.tag_redraw()


def _add_message(state, role: str, content: str, is_error: bool = False, code: str = "") -> None:
    """Append a message to the persistent UI transcript."""
    msg = state.messages.add()
    msg.role = role
    msg.content = content
    msg.is_error = is_error
    msg.code = code


def _assistant_content(prose: str, code: str) -> str:
    """Format an assistant turn (plan/prose + code) for the API conversation."""
    parts = []
    if prose:
        parts.append(prose)
    if code:
        parts.append(f"```python\n{code}\n```")
    return "\n\n".join(parts)


def _last_user_prompt(state) -> str:
    """Return the most recent user message, for error logging."""
    for i in range(len(state.messages) - 1, -1, -1):
        if state.messages[i].role == "user":
            return state.messages[i].content
    return ""


def _build_history(state) -> list[dict[str, str]]:
    """Build LLM conversation history from the persistent UI transcript."""
    history = []
    for m in state.messages:
        if m.role == "user":
            history.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            history.append({"role": "assistant", "content": _assistant_content(m.content, m.code)})

    # Keep the most recent exchanges to bound context size
    if len(history) > 40:
        history = history[-40:]
    return history


class AIASSIST_OT_send_message(Operator):
    bl_idname = "ai_assistant.send_message"
    bl_label = "Send Message"
    bl_description = "Send message to the AI assistant"

    def execute(self, context: bpy.types.Context) -> set[str]:
        state = context.scene.ai_assistant
        prompt_text = state.prompt.strip()

        if not prompt_text:
            self.report({"WARNING"}, "Please type a message first")
            return {"CANCELLED"}

        if state.is_busy:
            self.report({"WARNING"}, "AI assistant is already processing")
            return {"CANCELLED"}

        prefs = get_addon_preferences(context)

        # Validate API key
        if prefs.provider == "CLAUDE" and not prefs.claude_api_key:
            self.report({"ERROR"}, "Claude API key not set. Check addon preferences.")
            return {"CANCELLED"}
        elif prefs.provider == "OPENAI" and not prefs.openai_api_key:
            self.report({"ERROR"}, "OpenAI API key not set. Check addon preferences.")
            return {"CANCELLED"}
        elif prefs.provider == "KIMI" and not prefs.kimi_api_key:
            self.report({"ERROR"}, "Kimi API key not set. Check addon preferences.")
            return {"CANCELLED"}
        elif prefs.provider == "DEEPSEEK" and not prefs.deepseek_api_key:
            self.report({"ERROR"}, "DeepSeek API key not set. Check addon preferences.")
            return {"CANCELLED"}

        # Reset loop state (do NOT clear the transcript -- it persists across turns)
        global _request_id, _api_messages
        _request_id += 1
        state.step_count = 0
        state.retry_count = 0
        state.is_stopping = False

        # Add user message to the persistent transcript
        _add_message(state, "user", prompt_text)
        _log_write("user", prompt_text)

        # Clear input
        state.prompt = ""

        # Build the API conversation from the transcript (includes the new user message)
        _api_messages = _build_history(state)

        # Spawn the first call (planning phase)
        _spawn_llm_call(state, "planning")

        # Register a single timer that drives the whole plan -> step -> continue loop
        bpy.app.timers.register(_check_result_queue, first_interval=0.1)

        _redraw_views()
        return {"FINISHED"}


class AIASSIST_OT_stop(Operator):
    bl_idname = "ai_assistant.stop"
    bl_label = "Stop"
    bl_description = "Stop the current request"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _request_id
        state = context.scene.ai_assistant
        state.is_stopping = True
        state.phase = "idle"
        state.is_busy = False
        _request_id += 1  # invalidate any in-flight result

        # Drain stale results so the next request does not consume them
        while not _result_queue.empty():
            try:
                _result_queue.get_nowait()
            except queue.Empty:
                break

        _redraw_views()
        return {"FINISHED"}


class AIASSIST_OT_clear_chat(Operator):
    bl_idname = "ai_assistant.clear_chat"
    bl_label = "Clear Chat"
    bl_description = "Clear chat display (log is preserved in Text Editor)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _request_id
        state = context.scene.ai_assistant
        state.messages.clear()
        state.active_message_index = 0
        state.is_busy = False
        state.phase = "idle"
        state.step_count = 0
        state.retry_count = 0
        state.is_stopping = False
        _request_id += 1

        while not _result_queue.empty():
            try:
                _result_queue.get_nowait()
            except queue.Empty:
                break

        return {"FINISHED"}


class AIASSIST_OT_clear_log(Operator):
    bl_idname = "ai_assistant.clear_log"
    bl_label = "Clear Log"
    bl_description = "Clear the full conversation log"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if LOG_TEXT_NAME in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[LOG_TEXT_NAME])
        _get_log_text()  # Recreate empty
        # Clear panel UI too
        state = context.scene.ai_assistant
        state.messages.clear()
        state.is_busy = False
        state.phase = "idle"
        state.step_count = 0
        state.retry_count = 0
        state.is_stopping = False
        self.report({"INFO"}, "Log cleared")
        return {"FINISHED"}


class AIASSIST_OT_open_log(Operator):
    bl_idname = "ai_assistant.open_log"
    bl_label = "Open Log"
    bl_description = "Open the conversation log in Blender's Text Editor"

    def execute(self, context: bpy.types.Context) -> set[str]:
        log = _get_log_text()

        # If there's already a text editor open, just point it at the log
        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = log
                self.report({"INFO"}, "Log opened in Text Editor")
                return {"FINISHED"}

        # No text editor visible -- show a popup with instructions
        def draw_popup(self_popup: object, context: bpy.types.Context) -> None:
            self_popup.layout.label(text=f"Select '{LOG_TEXT_NAME}' in the text dropdown.")

        context.window_manager.popup_menu(draw_popup, title="Open Scripting workspace", icon="TEXT")
        return {"FINISHED"}


class AIASSIST_OT_copy_errors(Operator):
    bl_idname = "ai_assistant.copy_errors"
    bl_label = "Copy Errors"
    bl_description = "Copy collected errors to clipboard (formatted for GitHub issue)"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if ERROR_LOG_NAME not in bpy.data.texts:
            self.report({"INFO"}, "No errors collected")
            return {"CANCELLED"}

        log = bpy.data.texts[ERROR_LOG_NAME]
        text = log.as_string().strip()
        if not text:
            self.report({"INFO"}, "No errors collected")
            return {"CANCELLED"}

        context.window_manager.clipboard = text
        # Count errors
        count = text.count("## Error @")
        self.report({"INFO"}, f"{count} error(s) copied to clipboard")
        return {"FINISHED"}


class AIASSIST_OT_clear_errors(Operator):
    bl_idname = "ai_assistant.clear_errors"
    bl_label = "Clear Errors"
    bl_description = "Clear collected errors"

    def execute(self, context: bpy.types.Context) -> set[str]:
        if ERROR_LOG_NAME in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[ERROR_LOG_NAME])
        self.report({"INFO"}, "Errors cleared")
        return {"FINISHED"}


def _background_llm_call(
    request_id: int,
    provider: str,
    api_key: str | None,
    model: str,
    base_url: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Run LLM API call in a background thread. Puts (request_id, status, data) in the queue."""
    try:
        if provider in ("CLAUDE", "KIMI", "DEEPSEEK"):
            response = llm_client.call_anthropic(base_url, api_key, model, system_prompt, messages, extra_headers)
        elif provider == "OPENAI":
            response = llm_client.call_openai(api_key, model, system_prompt, messages)
        else:
            response = llm_client.call_ollama(base_url, model, system_prompt, messages)
        _result_queue.put((request_id, "success", response))
    except Exception as e:
        _result_queue.put((request_id, "error", str(e)))


def _spawn_llm_call(state, phase: str) -> None:
    """Spawn the next LLM call in the loop (does not register a timer)."""
    global _request_id
    prefs = get_addon_preferences()
    provider = prefs.provider
    api_key, model, base_url = _get_provider_config(prefs)
    extra_headers = _get_extra_headers(prefs)

    # Re-summarize the scene: it changed since the last step
    scene_ctx = scene_context.get_scene_summary()
    blender_version = ".".join(str(v) for v in bpy.app.version)
    system_prompt = llm_client.build_system_prompt(blender_version, scene_ctx, rich=state.rich_prompt)

    state.phase = phase
    state.is_busy = True

    thread = threading.Thread(
        target=_background_llm_call,
        args=(_request_id, provider, api_key, model, base_url, system_prompt, list(_api_messages), extra_headers),
        daemon=True,
    )
    thread.start()
    _redraw_views()


def _finish_loop(state) -> None:
    """Reset loop state and unlock the UI."""
    global _api_messages
    state.is_busy = False
    state.phase = "idle"
    state.is_stopping = False
    state.step_count = 0
    state.retry_count = 0
    _api_messages = []


def _is_done(text: str) -> bool:
    """Detect a standalone DONE signal (the reply has no code block)."""
    cleaned = "".join(ch for ch in text.strip().upper() if ch.isalnum() or ch == " ")
    words = cleaned.split()
    if not words:
        return False
    return words[0] in ("DONE", "COMPLETE", "FINISHED") and len(words) <= 3


def _handle_planning_response(state, text: str) -> None:
    """First turn: extract the plan + first step, execute it, then continue."""
    prose = code_execution.extract_prose(text)
    blocks = code_execution.extract_code_blocks(text)

    if not blocks:
        _add_message(state, "assistant", prose)
        _log_write("assistant", prose)
        _add_message(state, "system", "No code block received. Reply with a ```python block to run something.", is_error=True)
        _finish_loop(state)
        return

    code = blocks[0]
    _add_message(state, "assistant", prose, code=code)
    _log_write("assistant", _assistant_content(prose, code))
    _api_messages.append({"role": "assistant", "content": _assistant_content(prose, code)})

    _execute_and_continue(state, code)


def _handle_stepping_response(state, text: str) -> None:
    """Later turns: execute the next step, or finish on DONE."""
    prose = code_execution.extract_prose(text)
    blocks = code_execution.extract_code_blocks(text)

    if not blocks:
        if _is_done(text):
            _add_message(state, "system", "Task complete.")
            _log_write("system", "Task complete.")
        else:
            _add_message(state, "assistant", prose)
            _log_write("assistant", prose)
            _add_message(state, "system", "Stopped: no code block received.", is_error=True)
        _finish_loop(state)
        return

    code = blocks[0]
    _add_message(state, "assistant", prose, code=code)
    _log_write("assistant", _assistant_content(prose, code))
    _api_messages.append({"role": "assistant", "content": _assistant_content(prose, code)})

    _execute_and_continue(state, code)


def _execute_and_continue(state, code: str) -> None:
    """Execute one step's code, record the result, then continue or finish."""
    prefs = get_addon_preferences()
    success, stdout, error = code_execution.execute_code(code)

    if success:
        content = "Executed successfully." + (f"\nOutput:\n{stdout}" if stdout.strip() else "")
        _add_message(state, "system", content)
        _log_write("system", content)
        state.retry_count = 0
        continuation = (
            "Step executed successfully.\nOutput:\n" + stdout +
            "\n\nContinue with the next step. Reply with ONE ```python block "
            "for the next step, or reply exactly DONE if the task is complete."
        )
    else:
        content = f"Execution error:\n{error}"
        _add_message(state, "system", content, is_error=True)
        _log_write("system", content)
        _log_error(_last_user_prompt(state), code, error)

        if state.retry_count < prefs.max_retries:
            state.retry_count += 1
            continuation = code_execution.format_error_for_retry(code, error)
        else:
            _add_message(state, "system", f"Step failed after {prefs.max_retries} retries.", is_error=True)
            _finish_loop(state)
            return

    state.step_count += 1
    if state.step_count > prefs.max_steps:
        _add_message(state, "system", f"Step limit reached ({prefs.max_steps}).", is_error=True)
        _finish_loop(state)
        return

    _api_messages.append({"role": "user", "content": continuation})
    _spawn_llm_call(state, "stepping")


def _check_result_queue() -> float | None:
    """Timer callback: drives the plan -> step -> continue loop on the main thread."""
    scene = bpy.context.scene
    if not hasattr(scene, "ai_assistant"):
        return None
    state = scene.ai_assistant

    if _result_queue.empty():
        if state.is_stopping or state.phase == "idle":
            return None
        return 0.1

    request_id, status, data = _result_queue.get()

    # Discard results from a cancelled or superseded request
    if request_id != _request_id:
        return 0.1 if state.is_busy else None

    if state.is_stopping:
        _finish_loop(state)
        _redraw_views()
        return None

    if status == "error":
        _add_message(state, "system", f"API Error: {data}", is_error=True)
        _log_write("system", f"API Error: {data}")
        _finish_loop(state)
        _redraw_views()
        return None

    text = data
    if state.phase == "planning":
        _handle_planning_response(state, text)
    else:
        _handle_stepping_response(state, text)

    _redraw_views()
    return None if state.phase == "idle" else 0.1


class AIASSIST_OT_clear_polyhaven_cache(Operator):
    bl_idname = "ai_assistant.clear_polyhaven_cache"
    bl_label = "Clear Polyhaven Cache"
    bl_description = "Delete all cached Polyhaven model downloads"

    def execute(self, context: bpy.types.Context) -> set[str]:
        count, size_mb = polyhaven.clear_cache()
        self.report({"INFO"}, f"Cleared {count} cached models ({size_mb:.1f} MB)")
        return {"FINISHED"}


class AIASSIST_OT_clear_sketchfab_cache(Operator):
    bl_idname = "ai_assistant.clear_sketchfab_cache"
    bl_label = "Clear Sketchfab Cache"
    bl_description = "Delete all cached Sketchfab model downloads"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import sketchfab
        count, size_mb = sketchfab.clear_cache()
        self.report({"INFO"}, f"Cleared {count} cached models ({size_mb:.1f} MB)")
        return {"FINISHED"}


classes = (
    AIASSIST_OT_send_message,
    AIASSIST_OT_stop,
    AIASSIST_OT_clear_chat,
    AIASSIST_OT_clear_log,
    AIASSIST_OT_open_log,
    AIASSIST_OT_copy_errors,
    AIASSIST_OT_clear_errors,
    AIASSIST_OT_clear_polyhaven_cache,
    AIASSIST_OT_clear_sketchfab_cache,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
