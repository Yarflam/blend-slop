import bpy
from bpy.props import StringProperty, EnumProperty, IntProperty, BoolProperty
from bpy.types import AddonPreferences


def get_addon_preferences(context: bpy.types.Context | None = None) -> "AIAssistantPreferences":
    if context is None:
        context = bpy.context
    return context.preferences.addons[__package__].preferences


class AIAssistantPreferences(AddonPreferences):
    bl_idname = __package__

    provider: EnumProperty(
        name="Provider",
        items=[
            ("CLAUDE", "Claude (Anthropic)", "Use Anthropic Claude API"),
            ("OPENAI", "OpenAI", "Use OpenAI API (GPT-4o, etc.)"),
            ("KIMI", "Kimi (Moonshot)", "Use Kimi coding API (Anthropic-compatible)"),
            ("DEEPSEEK", "DeepSeek", "Use DeepSeek API (Anthropic-compatible)"),
            ("OLLAMA", "Ollama (Local)", "Use local Ollama instance"),
        ],
        default="CLAUDE",
        description="LLM provider to use",
    )

    claude_api_key: StringProperty(
        name="Claude API Key",
        subtype="PASSWORD",
        description="Anthropic API key (starts with sk-ant-)",
    )

    claude_model: EnumProperty(
        name="Claude Model",
        items=[
            ("claude-sonnet-4-20250514", "Claude Sonnet 4", "Fast and capable"),
            ("claude-opus-4-20250514", "Claude Opus 4", "Most capable"),
            ("claude-haiku-4-20250414", "Claude Haiku 4", "Fastest and cheapest"),
        ],
        default="claude-sonnet-4-20250514",
    )

    openai_api_key: StringProperty(
        name="OpenAI API Key",
        subtype="PASSWORD",
        description="OpenAI API key (starts with sk-)",
    )

    openai_model: EnumProperty(
        name="OpenAI Model",
        items=[
            ("gpt-4o", "GPT-4o", "Most capable"),
            ("gpt-4o-mini", "GPT-4o Mini", "Fast and cheap"),
            ("gpt-4.1", "GPT-4.1", "Latest GPT-4 variant"),
        ],
        default="gpt-4o",
    )

    kimi_api_key: StringProperty(
        name="Kimi API Key",
        subtype="PASSWORD",
        description="Kimi API key (starts with sk-kimi-)",
    )

    kimi_model: EnumProperty(
        name="Kimi Model",
        items=[
            ("k3", "K3", "Kimi K3 (api.kimi.ai/coding)"),
            ("kimi-for-coding", "Kimi for Coding", "Kimi for Coding (api.kimi.com/coding)"),
            ("kimi-coding", "Kimi Coding", "Kimi Coding alias (api.kimi.com/coding)"),
        ],
        default="k3",
    )

    kimi_base_url: StringProperty(
        name="Kimi Base URL",
        default="https://api.kimi.ai/coding",
        description="Kimi API base URL (use https://api.kimi.com/coding for kimi-for-coding)",
    )

    deepseek_api_key: StringProperty(
        name="DeepSeek API Key",
        subtype="PASSWORD",
        description="DeepSeek API key (starts with sk-)",
    )

    deepseek_model: EnumProperty(
        name="DeepSeek Model",
        items=[
            ("deepseek-v4-pro", "DeepSeek V4 Pro", "Most capable DeepSeek model"),
            ("deepseek-v4-flash", "DeepSeek V4 Flash", "Fast and cheap DeepSeek model"),
        ],
        default="deepseek-v4-pro",
    )

    deepseek_base_url: StringProperty(
        name="DeepSeek Base URL",
        default="https://api.deepseek.com/anthropic",
        description="DeepSeek Anthropic-compatible API base URL",
    )

    deepseek_long_context: BoolProperty(
        name="1M Context (Pro)",
        default=True,
        description="Request the 1M-token context beta for deepseek-v4-pro (anthropic-beta header)",
    )

    ollama_url: StringProperty(
        name="Ollama URL",
        default="http://localhost:11434",
        description="Ollama server URL",
    )

    ollama_model: StringProperty(
        name="Ollama Model",
        default="qwen2.5-coder:7b",
        description="Model name (e.g. qwen2.5-coder:7b, llama3.2, deepseek-coder-v2)",
    )

    sketchfab_api_key: StringProperty(
        name="Sketchfab API Key",
        subtype="PASSWORD",
        description="Sketchfab API token (get from sketchfab.com/settings/password)",
    )

    max_retries: IntProperty(
        name="Max Retries",
        default=3,
        min=0,
        max=10,
        description="Number of automatic retries per step on code execution error",
    )

    max_steps: IntProperty(
        name="Max Steps",
        default=15,
        min=1,
        max=50,
        description="Maximum number of execution steps per request (prevents runaway loops)",
    )

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout

        layout.prop(self, "provider")
        layout.separator()

        if self.provider == "CLAUDE":
            layout.prop(self, "claude_api_key")
            layout.prop(self, "claude_model")
        elif self.provider == "OPENAI":
            layout.prop(self, "openai_api_key")
            layout.prop(self, "openai_model")
        elif self.provider == "KIMI":
            layout.prop(self, "kimi_api_key")
            layout.prop(self, "kimi_model")
            layout.prop(self, "kimi_base_url")
        elif self.provider == "DEEPSEEK":
            layout.prop(self, "deepseek_api_key")
            layout.prop(self, "deepseek_model")
            layout.prop(self, "deepseek_base_url")
            layout.prop(self, "deepseek_long_context")
        elif self.provider == "OLLAMA":
            layout.prop(self, "ollama_url")
            layout.prop(self, "ollama_model")

        layout.separator()
        layout.prop(self, "sketchfab_api_key")
        layout.separator()
        layout.prop(self, "max_retries")
        layout.prop(self, "max_steps")


classes = (AIAssistantPreferences,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
