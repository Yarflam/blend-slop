import bpy
from bpy.types import Panel

from .preferences import get_addon_preferences


def _wrap_text(text: str, width_chars: int) -> list[str]:
    """Word-wrap text to fit panel width."""
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current_line = ""
        for word in words:
            if current_line and len(current_line) + 1 + len(word) > width_chars:
                lines.append(current_line)
                current_line = word
            else:
                current_line = f"{current_line} {word}" if current_line else word
        if current_line:
            lines.append(current_line)
    return lines


def _get_panel_width_chars(context: bpy.types.Context) -> int:
    """Estimate panel width in characters based on region width."""
    region = getattr(context, "region", None)
    if region is not None and region.width > 0:
        # Approximate: 7 pixels per character at default DPI
        return max(20, region.width // 7 - 2)
    for area in context.screen.areas:
        if area.type == "VIEW_3D":
            for r in area.regions:
                if r.type == "UI":
                    return max(20, r.width // 7 - 2)
    return 40


def _draw_chat_ui(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    """Shared chat UI used by both the viewport sidebar and Properties > Tool panels."""
    state = context.scene.ai_assistant
    width_chars = _get_panel_width_chars(context)

    # Collapsible settings
    prefs = get_addon_preferences(context)
    header, panel = layout.panel("AIASSIST_settings", default_closed=True)
    header.label(text="Settings")
    if panel:
        row = panel.row(align=True)
        row.prop(prefs, "provider", text="")
        if prefs.provider == "CLAUDE":
            row.prop(prefs, "claude_model", text="")
        elif prefs.provider == "OPENAI":
            row.prop(prefs, "openai_model", text="")
        elif prefs.provider == "KIMI":
            row.prop(prefs, "kimi_model", text="")
        elif prefs.provider == "DEEPSEEK":
            row.prop(prefs, "deepseek_model", text="")
        elif prefs.provider == "OLLAMA":
            row.prop(prefs, "ollama_model", text="")

        if prefs.provider == "CLAUDE":
            panel.prop(prefs, "claude_api_key", text="LLM Key")
        elif prefs.provider == "OPENAI":
            panel.prop(prefs, "openai_api_key", text="LLM Key")
        elif prefs.provider == "KIMI":
            panel.prop(prefs, "kimi_api_key", text="LLM Key")
            panel.prop(prefs, "kimi_base_url", text="URL")
        elif prefs.provider == "DEEPSEEK":
            panel.prop(prefs, "deepseek_api_key", text="LLM Key")
            panel.prop(prefs, "deepseek_base_url", text="URL")
            panel.prop(prefs, "deepseek_long_context")
        elif prefs.provider == "OLLAMA":
            panel.prop(prefs, "ollama_url", text="URL")

        panel.prop(prefs, "sketchfab_api_key", text="Sketchfab Key")

        panel.separator()

        row = panel.row(align=True)
        row.prop(state, "rich_prompt", text="Rich Prompt", icon="FILE_TEXT", toggle=True)
        row.operator("ai_assistant.open_log", text="Log", icon="TEXT")
        row.operator("ai_assistant.clear_log", text="", icon="TRASH")

        row = panel.row(align=True)
        row.operator("ai_assistant.copy_errors", text="Copy Errors", icon="COPYDOWN")
        row.operator("ai_assistant.clear_errors", text="", icon="TRASH")

        panel.label(text="Clear Cache:")
        row = panel.row(align=True)
        row.operator("ai_assistant.clear_polyhaven_cache", text="Polyhaven", icon="TRASH")
        row.operator("ai_assistant.clear_sketchfab_cache", text="Sketchfab", icon="TRASH")

    layout.separator()

    # Chat history -- full transcript (user, assistant plan/code, and results)
    if state.messages:
        row = layout.row()
        row.label(text="")
        row.operator("ai_assistant.clear_chat", text="", icon="X")

        chat_box = layout.box()
        col = chat_box.column(align=True)

        for msg in state.messages:
            if msg.role == "user":
                _draw_user_message(col, msg, width_chars)
            elif msg.role == "assistant":
                _draw_assistant_message(col, msg, width_chars)
            elif msg.role == "system":
                _draw_system_message(col, msg, width_chars)
    else:
        layout.label(text="Start a conversation...", icon="INFO")

    layout.separator()

    # Busy indicator
    if state.is_busy:
        row = layout.row()
        row.alert = True
        row.label(text="Thinking...", icon="SORTTIME")
        row.operator("ai_assistant.stop", text="Stop", icon="X")
        layout.separator()

    # Input area
    row = layout.row(align=True)
    row.prop(state, "prompt", text="")
    row.enabled = not state.is_busy
    send_row = layout.row()
    send_row.scale_y = 1.3
    send_row.enabled = not state.is_busy
    send_row.operator("ai_assistant.send_message", text="Send", icon="PLAY")


class AIASSIST_PT_main_panel(Panel):
    bl_label = "AI Assistant"
    bl_idname = "AIASSIST_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI Assistant"

    def draw(self, context: bpy.types.Context) -> None:
        _draw_chat_ui(self.layout, context)


class AIASSIST_PT_properties_panel(Panel):
    """Same chat, docked in the Properties editor's Tool tab (right column)."""

    bl_label = "AI Assistant"
    bl_idname = "AIASSIST_PT_properties_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "tool"

    def draw(self, context: bpy.types.Context) -> None:
        _draw_chat_ui(self.layout, context)


def _draw_user_message(col: bpy.types.UILayout, msg: object, width_chars: int) -> None:
    box = col.box()
    row = box.row()
    row.label(text="", icon="USER")
    row.label(text="You")
    for line in _wrap_text(msg.content, width_chars - 4):
        box.label(text=line)


def _draw_system_message(col: bpy.types.UILayout, msg: object, width_chars: int) -> None:
    box = col.box()
    if msg.is_error:
        box.alert = True
        row = box.row()
        row.label(text="", icon="ERROR")
        row.label(text="Error")
    else:
        row = box.row()
        row.label(text="", icon="CHECKMARK")
        row.label(text="Result")

    for line in _wrap_text(msg.content, width_chars - 4)[:15]:
        box.label(text=line)
    total_lines = len(_wrap_text(msg.content, width_chars - 4))
    if total_lines > 15:
        box.label(text=f"... ({total_lines - 15} more lines)")


def _draw_assistant_message(col: bpy.types.UILayout, msg: object, width_chars: int) -> None:
    """Draw the assistant's plan/prose and a compact preview of the generated code."""
    box = col.box()
    row = box.row()
    row.label(text="", icon="INFO")
    row.label(text="Assistant")

    if msg.content:
        for line in _wrap_text(msg.content, width_chars - 4):
            box.label(text=line)

    if msg.code:
        code_lines = msg.code.strip().split("\n")
        box.label(text=f"Code ({len(code_lines)} lines):")
        for line in code_lines[:8]:
            if len(line) > width_chars - 4:
                line = line[: width_chars - 4] + "…"
            box.label(text=line)
        if len(code_lines) > 8:
            box.label(text=f"... ({len(code_lines) - 8} more lines)")


classes = (AIASSIST_PT_main_panel, AIASSIST_PT_properties_panel)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
