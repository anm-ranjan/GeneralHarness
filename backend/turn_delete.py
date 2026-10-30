"""Deleting the latest prompt of a session when it never got a reply.

A prompt that was answered is part of the conversation context the model (and
FhGenie's browser chat) built on, so it can never be removed from here. A prompt
whose run failed, timed out or was stopped is different: run_agent ends with
`if interrupted or failed: del messages[turn_start_index:]`, so the model
context is already rolled back past it and removing it from the transcript
cannot make the transcript and the model disagree.

Pure functions over event objects (anything with `.type` and `.data`).
"""
from __future__ import annotations

# run_finished reasons whose turn the run loop rolls back out of the model
# context (`interrupted` or `failed` in run_agent). "max iterations reached" and
# "no content returned" are NOT here: those loops end normally, so the turn
# stays in the saved context and must stay in the transcript.
UNANSWERED_REASONS = frozenset({"interrupted", "error", "api timeout"})


def _text(event) -> str:
    return str((event.data or {}).get("text", ""))


def is_real_prompt(event) -> bool:
    """A user prompt that opens a turn: not a mid-run steering note, not a slash command."""
    if event.type != "user_message":
        return False
    if (event.data or {}).get("steering"):
        return False
    return not _text(event).lstrip().startswith("/")


def last_prompt_index(events) -> int | None:
    """Index within `events` of the latest real prompt, or None."""
    for index in range(len(events) - 1, -1, -1):
        if is_real_prompt(events[index]):
            return index
    return None


def _has_reply(events) -> bool:
    for event in events:
        if event.type != "assistant_message":
            continue
        data = event.data or {}
        if str(data.get("markdown") or data.get("text") or "").strip():
            return True
    return False


def assess_last_prompt(events) -> str:
    """Why the prompt that starts `events` cannot be deleted, or '' if it can.

    `events` runs from that prompt to the end of the log.
    """
    if not events or not is_real_prompt(events[0]):
        return "There is no prompt to delete."
    if _has_reply(events):
        return "This prompt already has a reply, so it is part of the conversation and cannot be deleted."
    # Slash commands that ran after the prompt end with reason "command"; they
    # say nothing about how the prompt's own run finished.
    reasons = [
        (e.data or {}).get("reason")
        for e in events
        if e.type == "run_finished" and (e.data or {}).get("reason") != "command"
    ]
    # No run_finished at all means the server stopped mid-run; the caller has
    # already checked that nothing is running now.
    finished = reasons[-1] if reasons else None
    if finished is None or finished in UNANSWERED_REASONS:
        return ""
    return (
        "This prompt's run finished without a reply but is part of the model's context, "
        "so it cannot be deleted."
    )
