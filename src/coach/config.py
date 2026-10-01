"""Central configuration for the conversation-coaching app.

All settings are environment-driven (see ``.env.example``). Nothing here talks
to Azure; this module only shapes values so the rest of the app stays testable.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment / ``.env``.

    Resource *names* are never assumed equal to deployment names or index
    names. Each is configured explicitly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Azure OpenAI Realtime (GA /openai/v1 protocol) ------------------
    azure_openai_endpoint: str = Field(
        default="https://shhchat.openai.azure.com",
        description="Base resource URL, e.g. https://shhchat.openai.azure.com",
    )
    # The realtime *deployment* name. NOT assumed equal to the resource name.
    azure_openai_realtime_deployment: str = Field(default="gpt-realtime-2.1")
    # Deployment used to transcribe the user's input audio (server-side VAD).
    # Enables the backend to read the user's words and drive retrieval itself.
    azure_openai_transcribe_deployment: str = Field(default="gpt-4o-mini-transcribe")
    # Optional API key. If empty, DefaultAzureCredential (Entra ID) is used.
    azure_openai_api_key: str = Field(default="")
    # Entra scope used to mint a bearer token when no api-key is provided.
    azure_openai_token_scope: str = Field(default="https://ai.azure.com/.default")

    # ---- Azure AI Search -------------------------------------------------
    azure_search_endpoint: str = Field(
        default="https://secondchat.search.windows.net",
    )
    # The index name. NOT assumed equal to the service name ("secondchat").
    azure_search_index: str = Field(default="")
    azure_search_api_key: str = Field(default="")
    azure_search_api_version: str = Field(default="2024-07-01")

    # Configurable field mappings (keyword-first grounding). These map the
    # index's own field names onto the roles the app needs.
    azure_search_id_field: str = Field(default="id")
    azure_search_content_field: str = Field(default="content")
    azure_search_title_field: str = Field(default="title")
    azure_search_url_field: str = Field(default="url")
    # Comma-separated list of fields to restrict keyword search to (optional).
    azure_search_search_fields: str = Field(default="")
    azure_search_top: int = Field(default=3, ge=1, le=50)

    # ---- Hybrid search (OFF until vectorizer/embedding confirmed) --------
    azure_search_use_hybrid: bool = Field(default=False)
    azure_search_vector_field: str = Field(default="")
    azure_openai_embedding_deployment: str = Field(default="text-embedding-ada-002")

    # ---- Coaching response policy ---------------------------------------
    # Default: silent TEXT coaching. Spoken mode is opt-in.
    coach_spoken_mode: bool = Field(default=False)
    # Whether the model auto-responds on VAD stop. Default OFF: the backend
    # explicitly drives search + response so answers are always grounded.
    coach_auto_response: bool = Field(default=False)
    coach_voice: str = Field(default="marin")
    coach_instructions: str = Field(
        default=(
            "You are a knowledgeable representative who understands Caldova "
            "(Caldova Pharmaceuticals) and the Caldova demo experience. You are "
            "speaking with people in a live, real-time voice conversation. Be "
            "warm, natural, and concise — like a helpful colleague who knows "
            "Caldova well. Ground factual claims about Caldova, its products, "
            "processes, and the demo in the provided GROUNDING context, and do "
            "not invent specifics (facts, figures, names, dates, steps) that "
            "aren't supported there. If you lack grounding for a detail, say so "
            "briefly or ask a clarifying question, then keep the conversation "
            "moving. Keep spoken replies short and natural."
        )
    )

    # ---- Privacy / persistence ------------------------------------------
    # Audio and transcripts are NOT persisted by default.
    coach_persist_transcripts: bool = Field(default=False)

    # ---- Server ----------------------------------------------------------
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

    @field_validator("azure_openai_endpoint", "azure_search_endpoint")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def client_secrets_url(self) -> str:
        """GA endpoint to mint an ephemeral client secret (server-side)."""
        return f"{self.azure_openai_endpoint}/openai/v1/realtime/client_secrets"

    @property
    def webrtc_calls_url(self) -> str:
        """GA WebRTC SDP-exchange endpoint (used by the browser)."""
        return f"{self.azure_openai_endpoint}/openai/v1/realtime/calls?webrtcfilter=on"

    @property
    def search_fields_list(self) -> list[str]:
        raw = self.azure_search_search_fields.strip()
        if not raw:
            return []
        return [f.strip() for f in raw.split(",") if f.strip()]

    @property
    def uses_api_key(self) -> bool:
        return bool(self.azure_openai_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the app reads the environment once."""
    return Settings()
