"""GA realtime event names and helpers for the sideband control channel.

These are the *modern* GA event names verified against Microsoft Learn
(2026-09). We deliberately avoid the deprecated preview names
(e.g. ``response.text.delta``).
"""

from __future__ import annotations

from typing import Any

# ---- Client -> server events (we build these and send to the model) --------
SESSION_UPDATE = "session.update"
CONVERSATION_ITEM_CREATE = "conversation.item.create"
RESPONSE_CREATE = "response.create"
RESPONSE_CANCEL = "response.cancel"

# ---- Server -> client events (we receive these, relayed over sideband) -----
SESSION_CREATED = "session.created"
SESSION_UPDATED = "session.updated"
INPUT_AUDIO_BUFFER_SPEECH_STARTED = "input_audio_buffer.speech_started"
INPUT_AUDIO_BUFFER_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
# Fires with server VAD when the user's audio turn is committed as an input
# item. This is the reliable "user turn complete" signal and does NOT depend on
# input-audio transcription being enabled.
INPUT_AUDIO_BUFFER_COMMITTED = "input_audio_buffer.committed"
# A conversation item was added. For a committed USER audio turn this is the
# event that actually fires (server VAD), so we use it (filtered to role=user)
# as the primary turn-complete trigger.
CONVERSATION_ITEM_ADDED = "conversation.item.added"
INPUT_TRANSCRIPTION_COMPLETED = (
    "conversation.item.input_audio_transcription.completed"
)
RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE = "response.function_call_arguments.done"
RESPONSE_OUTPUT_TEXT_DELTA = "response.output_text.delta"  # GA (was response.text.delta)
RESPONSE_OUTPUT_AUDIO_TRANSCRIPT_DELTA = "response.output_audio_transcript.delta"
RESPONSE_CREATED = "response.created"
RESPONSE_DONE = "response.done"
CONVERSATION_ITEM_DONE = "conversation.item.done"
ERROR = "error"


def build_search_tool() -> dict[str, Any]:
    """Function tool the model calls to request grounded retrieval."""
    return {
        "type": "function",
        "name": "search",
        "description": (
            "Search the grounding knowledge base for passages relevant to the "
            "user's latest statement. Always call this before offering advice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise search query derived from the conversation.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def build_function_call_output(call_id: str, output: str) -> dict[str, Any]:
    """Create the conversation item that returns a tool result by call_id.

    The ``call_id`` MUST match the model's ``function_call`` so the model can
    correlate the result with its request.
    """
    return {
        "type": CONVERSATION_ITEM_CREATE,
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }


def build_response_create(
    *,
    spoken: bool,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Ask the model to generate a response after retrieval completes.

    ``output_modalities`` is ``["text"]`` for silent coaching (default) or
    ``["audio", "text"]`` for spoken mode.
    """
    response: dict[str, Any] = {
        "output_modalities": ["audio", "text"] if spoken else ["text"],
    }
    if instructions:
        response["instructions"] = instructions
    return {"type": RESPONSE_CREATE, "response": response}


def build_response_cancel() -> dict[str, Any]:
    """Cancel the in-progress response (used on barge-in / interruption)."""
    return {"type": RESPONSE_CANCEL}


def build_session_tools_update(*, auto_response: bool) -> dict[str, Any]:
    """Register the search tool on the live session after connect."""
    return {
        "type": SESSION_UPDATE,
        "session": {
            "type": "realtime",
            "tools": [build_search_tool()],
            "tool_choice": "auto",
        },
    }
