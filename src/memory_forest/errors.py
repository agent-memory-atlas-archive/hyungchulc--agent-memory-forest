from __future__ import annotations

import functools
import os
import shlex
from collections.abc import Callable, Mapping
from typing import Any, TypeVar


# The next move a program follows after a refusal (section 15.4). Who can act decides it, and every document
# defines the four in these words. run carries the command; retry carries the seconds to wait.
MOVE_DEFINITIONS = {
    "fix": "the call itself is wrong, and a corrected call succeeds on the forest as it is",
    "retry": "the same call succeeds later without anyone doing anything",
    "run": "a command this caller may run now, which changes the state",
    "report": "only the owner or another account can change what is wrong (a file, a setting, an access right)",
}
NEXT_MOVES = ("fix", "report", "retry", "run")
# What a retry waits, and the total after which the caller reports instead, when the site names none.
RETRY_AFTER_SECONDS = 2.0
RETRY_STOP_SECONDS = 60.0

# Every refusal code the package can raise, and its next move: (next, command). A test reads every
# module and proves this table, the codes raised and docs/refusals.md are the same set, both ways.
REFUSALS: dict[str, tuple[str, str]] = {
    "about_invalid": ("fix", ""),
    "access_denied": ("report", ""),
    "address_unknown": ("fix", ""),
    "agent_ambiguous": ("fix", ""),
    "agent_name_invalid": ("fix", ""),
    "agent_needs_group": ("fix", ""),
    "agent_not_in_group": ("fix", ""),
    "agent_unplaceable": ("report", ""),
    "agent_unset": ("fix", ""),
    "already_a_tree": ("fix", ""),
    "already_ended": ("fix", ""),
    "archive_conflict": ("report", ""),
    "archive_copy_changed": ("report", ""),
    "archive_copy_mismatch": ("report", ""),
    "archive_copy_unreadable": ("report", ""),
    "archive_damaged": ("report", ""),
    "archive_not_found": ("fix", ""),
    "archive_page_changed": ("retry", ""),
    "archive_path_invalid": ("fix", ""),
    "archive_proof_failed": ("report", ""),
    "archive_readback_failed": ("report", ""),
    "archive_reason_missing": ("fix", ""),
    "archive_unsupported": ("fix", ""),
    "arguments_invalid": ("fix", ""),
    "baseline_invalid": ("fix", ""),
    "branch_exists": ("fix", ""),
    "branch_holds_other_subjects": ("fix", ""),
    "branch_missing": ("fix", ""),
    "branch_not_empty": ("fix", ""),
    "byte_limit_exceeded": ("report", ""),
    "casefold_collision": ("report", ""),
    "check_unknown": ("fix", ""),
    "cite_line_moved": ("retry", ""),
    "collection_id_taken": ("fix", ""),
    "collection_page_taken": ("report", ""),
    "config_mismatch": ("report", ""),
    "context_source_invalid": ("report", ""),
    "context_too_large": ("report", ""),
    "credential_candidate_invalid": ("fix", ""),
    "credential_key_exposed": ("report", ""),
    "credential_key_invalid": ("report", ""),
    "credential_key_missing": ("report", ""),
    "credential_ledger_unsafe": ("report", ""),
    "credentials_archive_mismatch": ("report", ""),
    "credentials_fix_incomplete": ("report", ""),
    "credentials_path_invalid": ("fix", ""),
    "daily_provenance_mismatch": ("fix", ""),
    "decision_home_invalid": ("fix", ""),
    "decisions_invalid": ("fix", ""),
    "demote_several_leaves": ("report", ""),
    "demote_standing": ("fix", ""),
    "demote_unsupported": ("fix", ""),
    "description_missing": ("fix", ""),
    "directory_count_exceeded": ("report", ""),
    "directory_depth_exceeded": ("report", ""),
    "directory_entry_count_exceeded": ("report", ""),
    "duplicate_daily_entry_id": ("fix", ""),
    "duplicate_query_plan_probe": ("fix", ""),
    "embeddings_failed": ("report", ""),
    "empty_jsonl": ("report", ""),
    "empty_query": ("fix", ""),
    "entry_is_subject": ("report", ""),
    "entry_name_taken": ("fix", ""),
    "entry_unknown": ("fix", ""),
    "file_busy": ("retry", ""),
    "file_count_exceeded": ("report", ""),
    "file_open_failed": ("report", ""),
    "file_too_large": ("report", ""),
    "filesystem_error": ("report", ""),
    "fold_into_itself": ("fix", ""),
    "fold_tree_holds_subjects": ("fix", ""),
    "fold_tree_loose_pages": ("fix", ""),
    "fold_tree_type_missing": ("fix", ""),
    "fold_tree_unregistered": ("fix", ""),
    "folder_not_empty": ("fix", ""),
    "forest_identity_mismatch": ("fix", ""),
    "forest_identity_missing": ("report", ""),
    "forest_invalid": ("report", ""),
    "fts5_unavailable": ("report", ""),
    "group_full": ("report", ""),
    "group_unknown": ("report", ""),
    "hook_incomplete": ("fix", ""),
    "hook_invalid": ("fix", ""),
    "hook_timeout": ("retry", ""),
    "hook_too_large": ("fix", ""),
    "id_taken": ("fix", ""),
    "index_busy": ("retry", ""),
    "index_cannot_open": ("report", ""),
    "index_corrupt": ("run", ""),
    "index_disk_full": ("report", ""),
    "index_failed": ("report", ""),
    "index_io_error": ("report", ""),
    "index_leaf_without_references": ("report", ""),
    "index_missing": ("run", "forest index build"),
    "index_not_found": ("run", ""),
    "index_permissions": ("report", ""),
    "index_read_only": ("report", ""),
    "index_schema_mismatch": ("run", ""),
    "index_stale": ("run", ""),
    "index_unreadable": ("run", "forest index reset"),
    "intake_adapter_unknown": ("fix", ""),
    "intake_adapter_unset": ("fix", ""),
    "intake_agent_invalid": ("fix", ""),
    "intake_agent_unset": ("fix", ""),
    "intake_arguments": ("fix", ""),
    "intake_origin_invalid": ("fix", ""),
    "intake_path_unset": ("fix", ""),
    "intake_rename_exists": ("fix", ""),
    "intake_role_invalid": ("fix", ""),
    "intake_session_invalid": ("fix", ""),
    "intake_text_empty": ("fix", ""),
    "intake_turn_rejected": ("report", ""),
    "internal_error": ("report", ""),
    "interrupted": ("retry", ""),
    "into_missing": ("fix", ""),
    "invalid_arguments": ("fix", ""),
    "invalid_config": ("report", ""),
    "invalid_daily_date": ("fix", ""),
    "invalid_daily_machine_block": ("report", ""),
    "invalid_daily_path": ("fix", ""),
    "invalid_daily_plan": ("fix", ""),
    "invalid_document_path": ("fix", ""),
    "invalid_istm_path": ("fix", ""),
    "invalid_jsonl": ("report", ""),
    "invalid_jsonl_event": ("report", ""),
    "invalid_life_archive_path": ("fix", ""),
    "invalid_limit": ("fix", ""),
    "invalid_lock_name": ("fix", ""),
    "invalid_ltm_path": ("fix", ""),
    "invalid_mtm_path": ("fix", ""),
    "invalid_path": ("fix", ""),
    "invalid_promotion_plan": ("fix", ""),
    "invalid_query_plan": ("fix", ""),
    "invalid_query_plan_probe": ("fix", ""),
    "invalid_receipt": ("report", ""),
    "invalid_retrieval_config": ("report", ""),
    "invalid_route_segment": ("fix", ""),
    "invalid_stm_path": ("fix", ""),
    "invalid_structured_sweep_plan": ("fix", ""),
    "invalid_transaction_state": ("report", ""),
    "invalid_utf8": ("fix", ""),
    "invalid_write_operation": ("report", ""),
    "invalid_write_plan": ("fix", ""),
    "invalid_xltm_path": ("fix", ""),
    "journal_invalid": ("report", ""),
    "kind_invalid": ("fix", ""),
    "lane_fix_lines": ("report", ""),
    "layer_number_mismatch": ("fix", ""),
    "leaf_name_clash": ("fix", ""),
    "leaf_name_invalid": ("fix", ""),
    "link_count_exceeded": ("report", ""),
    "lint_code_invalid": ("fix", ""),
    "lint_code_unknown": ("fix", ""),
    "lock_already_held": ("fix", ""),
    "lock_busy": ("retry", ""),
    "lock_release_failed": ("report", ""),
    "lock_token_invalid": ("report", ""),
    "lock_unsupported": ("report", ""),
    "log_agent_unset": ("fix", ""),
    "log_caller_fields": ("fix", ""),
    "log_read_only": ("report", ""),
    "log_read_timeout": ("retry", ""),
    "log_text_from_stdin": ("fix", ""),
    "log_too_large": ("fix", ""),
    "maintenance_lock_busy": ("retry", ""),
    "mcp_agent_unset": ("fix", ""),
    "mcp_bind": ("fix", ""),
    "mcp_group_invalid": ("fix", ""),
    "mcp_reader_conflict": ("fix", ""),
    "mcp_token_invalid": ("report", ""),
    "mcp_token_missing": ("report", ""),
    "mcp_token_mode": ("report", ""),
    "member_source_failed": ("report", ""),
    "merge_collection": ("fix", ""),
    "merge_into_smaller": ("fix", ""),
    "merge_same": ("fix", ""),
    "merge_tree_unsupported": ("fix", ""),
    "missing_daily_source": ("fix", ""),
    "missing_parent": ("fix", ""),
    "missing_plan_input": ("fix", ""),
    "missing_write_parent": ("report", ""),
    "move_record_invalid": ("fix", ""),
    "move_to_same_place": ("fix", ""),
    "name_invalid": ("fix", ""),
    "name_missing": ("fix", ""),
    "name_taken": ("fix", ""),
    "no_forest": ("fix", ""),
    "non_regular_file": ("report", ""),
    "noncanonical_layer_directory": ("fix", ""),
    "noncanonical_route_segment": ("fix", ""),
    "noncanonical_write_path": ("report", ""),
    "not_a_collection_page": ("fix", ""),
    "not_a_leaf": ("fix", ""),
    "not_a_leaf_path": ("fix", ""),
    "not_a_slug": ("fix", ""),
    "not_an_agent_tree": ("report", ""),
    "not_initialized": ("report", ""),
    "numbers_page_changed": ("retry", ""),
    "numbers_path_invalid": ("fix", ""),
    "numbers_star_incomplete": ("report", ""),
    "operation_unknown": ("fix", ""),
    "out_of_scope": ("fix", ""),
    "page_changed_meanwhile": ("retry", ""),
    "page_is_home": ("fix", ""),
    "page_missing": ("fix", ""),
    "page_removed": ("fix", ""),
    "path_escape": ("fix", ""),
    "path_has_no_parent": ("fix", ""),
    "path_not_found": ("fix", ""),
    "path_not_in_forest": ("fix", ""),
    "plan_changed": ("retry", ""),
    "plan_check_failed": ("report", ""),
    "plan_file_invalid": ("fix", ""),
    "plan_not_found": ("fix", ""),
    "plan_too_large": ("fix", ""),
    "plugin_failed": ("report", ""),
    "postwrite_audit_failed": ("report", ""),
    "postwrite_validation_failed": ("report", ""),
    "preflight_audit_failed": ("report", ""),
    "preflight_validation_failed": ("report", ""),
    "promote_collection": ("fix", ""),
    "query_failed": ("report", ""),
    "query_plan_changed": ("retry", ""),
    "query_plan_input_unavailable": ("fix", ""),
    "query_plan_not_found": ("fix", ""),
    "query_plan_open_failed": ("report", ""),
    "query_plan_too_large": ("fix", ""),
    "query_too_long": ("fix", ""),
    "read_failed": ("report", ""),
    "read_lines": ("fix", ""),
    "read_lines_empty": ("fix", ""),
    "reader_conflict": ("fix", ""),
    "reason_missing": ("fix", ""),
    "receipt_capacity_exceeded": ("report", ""),
    "receipt_collision": ("retry", ""),
    "receipt_conflict": ("report", ""),
    "receipt_count_exceeded": ("report", ""),
    "receipt_state_mismatch": ("report", ""),
    "receipt_too_large": ("report", ""),
    "receipt_write_failed": ("report", ""),
    "record_missing": ("fix", ""),
    "record_page_changed": ("retry", ""),
    "record_path_invalid": ("fix", ""),
    "record_trim_unsafe": ("report", ""),
    "registry_invalid": ("run", "forest registry build"),
    "registry_missing": ("run", "forest registry build"),
    "registry_unreadable": ("report", ""),
    "rename_collection": ("fix", ""),
    "rename_nothing": ("fix", ""),
    "rename_to_same": ("fix", ""),
    "rename_tree_unsupported": ("fix", ""),
    "repeats_removal_unsafe": ("report", ""),
    "role_set": ("report", ""),
    "rollback_failed": ("report", ""),
    "root_exists": ("fix", ""),
    "root_not_directory": ("fix", ""),
    "root_not_empty": ("fix", ""),
    "root_not_found": ("fix", ""),
    "root_not_owned": ("report", ""),
    "scan_failed": ("report", ""),
    "scope_widened": ("fix", ""),
    "search_empty": ("fix", ""),
    "search_limit": ("fix", ""),
    "settings_invalid": ("report", ""),
    "settings_unpatched": ("report", ""),
    "settings_unwritable": ("report", ""),
    "source_unknown": ("fix", ""),
    "standing_invalid": ("fix", ""),
    "state_permissions": ("report", ""),
    "stream_empty": ("fix", ""),
    "stream_fields_differ": ("fix", ""),
    "stream_in_several_folders": ("fix", ""),
    "stream_kinds_differ": ("fix", ""),
    "stream_leaf_empty": ("fix", ""),
    "stream_leaf_not_a_list": ("fix", ""),
    "stream_names_differ": ("fix", ""),
    "stream_other_subject": ("fix", ""),
    "stream_page_taken": ("fix", ""),
    "structured_preimage_mismatch": ("fix", ""),
    "structured_snapshot_invalid": ("report", ""),
    "structured_snapshot_mismatch": ("fix", ""),
    "subject_has_no_leaf": ("report", ""),
    "subject_unknown": ("fix", ""),
    "subtopic_invalid": ("report", ""),
    "successor_missing": ("fix", ""),
    "symlink_forbidden": ("report", ""),
    "takeover_collection_conflict": ("report", ""),
    "target_exists": ("fix", ""),
    "time_phrase_empty": ("fix", ""),
    "time_phrase_table_invalid": ("report", ""),
    "time_phrase_unknown": ("fix", ""),
    "tool_failed": ("report", ""),
    "transaction_conflict": ("fix", ""),
    "transcript_deferred": ("report", ""),
    "transcript_missing": ("report", ""),
    "tree_exists": ("fix", ""),
    "tree_not_empty": ("fix", ""),
    "tree_type_not_allowed": ("fix", ""),
    "tree_unknown": ("fix", ""),
    "turn_id_reused": ("fix", ""),
    "type_collection": ("fix", ""),
    "type_unchanged": ("fix", ""),
    "type_unknown": ("fix", ""),
    "unjournaled_parent_directory": ("report", ""),
    "unknown_command": ("fix", ""),
    "unknown_layer": ("fix", ""),
    "unsafe_daily_directory": ("report", ""),
    "unsafe_daily_entry": ("report", ""),
    "unsafe_existing_path": ("report", ""),
    "unsafe_index_path": ("report", ""),
    "unsafe_index_staging": ("report", ""),
    "unsafe_lock_parent": ("report", ""),
    "unsafe_mutation_target": ("report", ""),
    "unsafe_parent_directory": ("report", ""),
    "unsafe_plan_permissions": ("report", ""),
    "unsafe_plan_source": ("report", ""),
    "unsafe_query_plan_source": ("report", ""),
    "unsafe_receipt_entry": ("report", ""),
    "unsafe_receipts_directory": ("report", ""),
    "unsafe_state_directory": ("report", ""),
    "unsafe_state_entry": ("report", ""),
    "unsafe_transcript": ("fix", ""),
    "unsafe_write_journal": ("report", ""),
    "unsupported_file_type": ("fix", ""),
    "unsupported_layer": ("report", ""),
    "unsupported_query_plan_schema": ("fix", ""),
    "write_journal_exists": ("report", ""),
    "write_journal_failed": ("report", ""),
    "write_readback_failed": ("report", ""),
    "write_recovery_failed": ("report", ""),
    "write_target_escape": ("report", ""),
    "write_transaction_too_large": ("report", ""),
}


class MemoryForestError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        next_step: str | None = None,
        command: str | None = None,
        after_seconds: float | None = None,
        waited_seconds: float | None = None,
        stop_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        # Section 15.4: a refusal a program can follow says its next move. ``run`` carries the command,
        # ``retry`` the seconds to wait before the same call again, and ``report`` means the message is
        # what to tell the owner, because no command helps. A refusal with no next move has none of them.
        # On retry, ``waited_seconds`` is how long this call already waited and ``stop_after_seconds`` is
        # the total after which the caller reports instead of calling again.
        # A site that names no next move gets the one REFUSALS gives its code.
        listed = REFUSALS.get(code)
        if next_step is None and listed is not None:
            next_step = listed[0]
        if command is None and listed is not None and next_step == listed[0] == "run" and listed[1]:
            command = listed[1]
        if next_step == "retry":
            after_seconds = RETRY_AFTER_SECONDS if after_seconds is None else after_seconds
            stop_after_seconds = RETRY_STOP_SECONDS if stop_after_seconds is None else stop_after_seconds
        self.next_step = next_step
        self.command = command
        self.after_seconds = after_seconds
        self.waited_seconds = waited_seconds
        self.stop_after_seconds = stop_after_seconds

    def as_dict(self) -> dict[str, Any]:
        """The one shape of a refusal: ``code``, ``message``, ``details`` and, when there is one, the next move."""
        body: dict[str, Any] = {
            "code": self.code,
            "details": dict(sorted(self.details.items())),
            "message": self.message,
        }
        if self.command:
            body["command"] = self.command
        if self.next_step:
            body["next"] = self.next_step
        if self.after_seconds is not None:
            body["after_seconds"] = self.after_seconds
        if self.stop_after_seconds is not None:
            body["stop_after_seconds"] = self.stop_after_seconds
        if self.waited_seconds is not None:
            body["waited_seconds"] = self.waited_seconds
        return body


# The codes of the older index that a rebuild cures. Their command names the forest the call named, so the
# function that was given that root fills it in (``with_rebuild``); the table gives them no command.
REBUILD_CODES = frozenset({"index_corrupt", "index_not_found", "index_schema_mismatch", "index_stale"})

_Function = TypeVar("_Function", bound=Callable[..., Any])


def with_rebuild(function: _Function) -> _Function:
    """A function of the older index whose refusals of ``REBUILD_CODES`` carry the command that rebuilds the
    index of the forest it was given: ``memory-forest index <that root, shell-quoted>``."""

    @functools.wraps(function)
    def rebuilding(root: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return function(root, *args, **kwargs)
        except MemoryForestError as exc:
            if exc.code in REBUILD_CODES and exc.next_step == "run" and not exc.command:
                exc.command = f"memory-forest index {shlex.quote(os.fspath(root))}"
            raise

    return rebuilding  # type: ignore[return-value]


# ─────────────────────────────── the edge: what the door answered, as a refusal ───────────────────────────────

_ACCESS_WORDS = {"read": "read", "list": "list", "enter": "enter", "write": "write in"}


def shown_place(path: str | os.PathLike[str], root: str | os.PathLike[str] | None) -> str:
    """``path`` as a refusal may name it: forest-relative inside ``root``, and words for the forest folder, a
    folder above it and anything else. No name of a place outside the forest leaves: it is the machine's."""
    text = os.fspath(path).rstrip("/") or "/"
    if root is not None:
        base = os.fspath(root).rstrip("/") or "/"
        if text == base:
            return "the forest folder"
        if text.startswith(base + "/"):
            return text[len(base) + 1 :]
        if base.startswith(text + "/") or text == "/":
            return "a folder above the forest folder"
    return "a path outside the forest"


def door_refusal(exc: OSError, *, root: str | os.PathLike[str] | None = None) -> MemoryForestError:
    """An outcome of the door that the code did not decide on, as a refusal by its kind (design, section 15.4).

    ``denied``: ``access_denied`` (report), naming what cannot be read, listed, entered or written.
    ``changed``, ``busy`` and ``absent`` (a file that went away while the call used it): ``file_busy`` (retry).
    ``link``: ``symlink_forbidden``; ``not_a_file``: ``non_regular_file``; ``not_a_folder``:
    ``unsafe_existing_path``; ``other``: ``filesystem_error`` with the error's name (all ``report``).
    What a refusal names is forest-relative inside ``root``, or words for any other place (``shown_place``):
    never a path of the machine.
    An ``OSError`` that did not come through the door is read by its error number first.
    """
    from . import system

    outcome = exc if isinstance(exc, system.Outcome) else system.outcome_of(exc)
    place = shown_place(outcome.where(), root)
    kind = outcome.kind
    details: dict[str, Any] = {"kind": kind, "path": place}
    if kind == "denied":
        access = outcome.access if outcome.access in _ACCESS_WORDS else "read"
        details["access"] = access
        return MemoryForestError(
            "access_denied",
            f"This account cannot {_ACCESS_WORDS[access]} {place}. The owner gives this account {access} access "
            "to it.",
            details=details,
            next_step="report",
        )
    if kind in ("changed", "busy", "absent"):
        what = {
            "changed": "changed while it was used",
            "busy": "was in use",
            "absent": "went away while it was used",
        }[kind]
        return MemoryForestError(
            "file_busy", f"{place} {what}. The same call answers later.", details=details, next_step="retry"
        )
    if kind == "link":
        return MemoryForestError(
            "symlink_forbidden", f"Symlinks are not allowed inside a Memory Forest; {place} is one.",
            details=details, next_step="report",
        )
    if kind == "not_a_file":
        return MemoryForestError(
            "non_regular_file", f"{place} is not a regular file.", details=details, next_step="report"
        )
    if kind == "not_a_folder":
        return MemoryForestError(
            "unsafe_existing_path", f"{place} is not a folder.", details=details, next_step="report"
        )
    details["error"] = outcome.error
    return MemoryForestError(
        "filesystem_error",
        f"{place} could not be used: the system answered {outcome.error}. Tell the owner.",
        details=details,
        next_step="report",
    )
