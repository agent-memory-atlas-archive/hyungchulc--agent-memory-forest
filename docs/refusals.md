# Refusal codes

Every refusal the package can raise is one of these codes. A test reads every module of the package,
collects every code a refusal is made with, and proves that this table and the table in the code
(`errors.REFUSALS`) are the same set, both ways, with the same next move. A code this table does not
hold cannot be raised, and a row the code never raises cannot stay.

The next move is what a program does after the refusal:

- `run`: run `command`; it cures the cause. A command with `<...>` is a template to fill in.
- `retry`: make the same call again after `after_seconds`, and report once the sum of each answer's
  `waited_seconds` and `after_seconds` reaches `stop_after_seconds`.
- `report`: no command of this caller helps; tell the owner the message. When the cause is a folder
  this account cannot write, the message names what an account that can write would run.
- `fix`: the call itself is wrong; change what the message names and call again. The same call gets
  the same refusal.

A site that names no next move gets its code's move from this table. `index_protocol` is internal: it
is how the index asks itself to try a read again, it is never sent, and it is not in this table. The
"When" column is the message of the first place the code is raised, with `…` where the message
names a value.

| Code | Next | Command | When |
| --- | --- | --- | --- |
| `about_invalid` | `fix` |  | `none` stands alone in `about:`. |
| `address_unknown` | `fix` |  | That address is not a section of the page. |
| `agent_ambiguous` | `fix` |  | `…` has more than one member that name could be. Pass the member's id. |
| `agent_is_group` | `fix` |  | `…` is the group, not an agent. Pass a member. |
| `agent_name_invalid` | `fix` |  | That name cannot be an agent. Pass a member's id. |
| `agent_near_miss` | `fix` |  | `…` is one edit from `…` (…). A typing slip does not make another agent. Pass agent …. |
| `agent_needs_group` | `fix` |  | --as is the reader. A caller's name belongs with --as-group, and it cannot change --as. |
| `agent_not_in_group` | `fix` |  | `…` is not a member of `…`. Call again with one of them. |
| `agent_outside_group` | `fix` |  | The name is an agent outside the group, or one edit away from one. |
| `agent_unset` | `fix` |  | Name the caller with --agent. |
| `already_a_tree` | `fix` |  | The subject is a tree already. |
| `already_ended` | `fix` |  | The agent is retired already. |
| `archive_conflict` | `report` |  | An archive copy with other bytes already holds this name. |
| `archive_copy_changed` | `report` |  | The archive copy no longer matches its manifest hash. |
| `archive_copy_mismatch` | `report` |  | The archive copy does not hold the page; not written. |
| `archive_copy_unreadable` | `report` |  | The archive copy cannot be read. |
| `archive_not_found` | `fix` |  | The page is not in the archive manifest. |
| `archive_page_changed` | `retry` |  | The page changed while it was archived; it stays in the forest. |
| `archive_path_invalid` | `fix` |  | Only pages of the four forest layers are archived. |
| `archive_readback_failed` | `report` |  | The archive copy did not read back to the page's SHA-256; nothing was archived. |
| `archive_reason_missing` | `fix` |  | An archived page needs a reason. |
| `archive_unsupported` | `fix` |  | Leaves, collection pages and emptied boards are archived this way. |
| `arguments_invalid` | `fix` |  | An argument fails the tool's input schema (protocol 2025-11-25). |
| `baseline_invalid` | `fix` |  | The file is not a forest lint baseline. |
| `branch_exists` | `fix` |  | A branch of that name already exists. |
| `branch_holds_other_subjects` | `fix` |  | The branch holds pages of other subjects; move them first. |
| `branch_missing` | `fix` |  | The target branch has no board; promote the subject or make the branch first. |
| `branch_not_empty` | `fix` |  | A board is archived only when its folder is empty. |
| `byte_limit_exceeded` | `report` |  | The forest exceeds the configured total-byte limit. |
| `casefold_collision` | `report` |  | A differently cased filesystem entry collides with a lock path. |
| `check_unknown` | `fix` |  | No such check. |
| `cite_line_moved` | `retry` |  | A current-state line is not where the pass read it. |
| `collection_id_taken` | `fix` |  | The collection's id is held by another subject. |
| `collection_page_taken` | `fix` |  | A month page the cut would make already exists. |
| `config_invalid` | `fix` |  | …: … |
| `config_mismatch` | `report` |  | The forest configuration does not match this schema. |
| `context_source_invalid` | `report` |  | The Structured context source has an invalid trail collection. |
| `context_too_large` | `report` |  | The selected Structured context exceeds its document bound. |
| `credential_candidate_invalid` | `fix` |  | Name a candidate as PATH:LINE:PLACE. |
| `credential_key_exposed` | `report` |  | The ledger key must be readable by its owner only (mode 0600). |
| `credential_key_invalid` | `report` |  | The ledger key is not the size it was made. |
| `credential_key_missing` | `report` |  | The ledger holds judgements but its key is gone; the values it names can no longer be recognised. |
| `credential_ledger_unsafe` | `report` |  | A ledger file must be a regular file, never a link. |
| `credentials_archive_mismatch` | `report` |  | The archive copy does not hold the page as it was; not written. |
| `credentials_fix_incomplete` | `report` |  | A sign-in value would still stand; not written. |
| `credentials_path_invalid` | `fix` |  | Only pages of the four forest layers are masked. |
| `daily_provenance_mismatch` | `fix` |  | Promotion provenance does not exactly bind the selected Daily commits. |
| `decision_home_invalid` | `fix` |  | A decision's home is one of the leaves that hold the line. |
| `decisions_invalid` | `fix` |  | A decisions file is a list of decisions (or an object with `decisions`). |
| `demote_several_leaves` | `fix` |  | A subject of several leaves is merged into one leaf before it is demoted. |
| `demote_standing` | `fix` |  | A standing line keeps the subject a leaf whatever its days. |
| `demote_unsupported` | `fix` |  | A collection or an agent is never folded into a collection. |
| `description_missing` | `fix` |  | A new tree needs a description of what lives there. |
| `directory_count_exceeded` | `report` |  | The forest exceeds the configured directory-count limit. |
| `directory_depth_exceeded` | `report` |  | The forest exceeds the configured directory-depth limit. |
| `directory_entry_count_exceeded` | `report` |  | A forest directory exceeds the traversal budget. |
| `duplicate_daily_entry_id` | `fix` |  | A Daily entry identifier appears more than once. |
| `duplicate_query_plan_probe` | `fix` |  | QueryPlan probe queries must be unique. |
| `embeddings_failed` | `report` |  | The embedding provider returned a different number of vectors than texts. |
| `empty_jsonl` | `fix` |  | An ISTM event stream must contain at least one JSON object. |
| `empty_query` | `fix` |  | The query must contain at least one searchable word. |
| `entry_is_subject` | `fix` |  | The entry's id is already a registered subject. |
| `entry_name_taken` | `fix` |  | An entry of this name is already on the collection. |
| `entry_unknown` | `fix` |  | No collection page has this entry. |
| `file_count_exceeded` | `report` |  | The forest exceeds the configured file-count limit. |
| `file_open_failed` | `report` |  | A forest file could not be opened safely. |
| `file_too_large` | `report` |  | A forest file grew beyond the configured byte limit while reading. |
| `filesystem_error` | `report` |  | A local filesystem operation failed safely. |
| `fold_into_itself` | `fix` |  | A tree does not fold into itself. |
| `fold_tree_holds_subjects` | `fix` |  | The tree holds other subjects; move their branches first. |
| `fold_tree_loose_pages` | `fix` |  | The tree holds pages that are no leaf of one of its branches. |
| `fold_tree_type_missing` | `fix` |  | An agent folded into a branch is no tree any more and takes another type (--type). |
| `fold_tree_unregistered` | `fix` |  | Only a tree with its own registry line (level tree) folds into a branch. |
| `folder_not_empty` | `fix` |  | The subject's folder already holds pages but has no board. |
| `forest_identity_mismatch` | `fix` |  | The Daily plan is bound to a different Memory Forest. |
| `forest_identity_missing` | `report` |  | This forest predates write identities; canonical writes require a forest_id created by a supported migration or new initialization. |
| `forest_invalid` | `report` |  | Indexing requires a forest that passes validation and audit. |
| `fts5_unavailable` | `report` |  | The local SQLite FTS5 index could not be built. |
| `group_unknown` | `report` |  | There is no group `…`. The owner declares [agents.groups.…] in forest.toml. |
| `hook_incomplete` | `fix` |  | The hook payload has no transcript path. |
| `hook_invalid` | `fix` |  | The hook payload is not a JSON object. |
| `hook_timeout` | `retry` |  | The hook payload did not arrive. |
| `hook_too_large` | `fix` |  | The hook payload is larger than intake reads. |
| `id_taken` | `fix` |  | A subject or entry already has this id. |
| `index_busy` | `retry` |  | The index lock is held, or the index file is in use or changed while it was read. |
| `index_cannot_open` | `report` |  | The .forest folder is not a directory. |
| `index_corrupt` | `run` | `memory-forest index <root>` | The local index contains an incomplete structured trail; rebuild it. |
| `index_disk_full` | `report` |  | The disk that holds the forest is full, so the search index cannot be written. Free space on that disk, then ask again. Do not reset the index: building it again needs more room than it holds now. |
| `index_failed` | `report` |  | The local SQLite FTS5 index could not be built. |
| `index_io_error` | `report` |  | … The locking protocol failed, and this call already waited … seconds. |
| `index_leaf_without_references` | `report` |  | The index leaf keeps its reference lines in no section. |
| `index_missing` | `run` | `forest index build` | The forest has no search index. Run `forest index build`. |
| `index_not_found` | `run` | `memory-forest index <root>` | Build the local index before querying it. |
| `index_permissions` | `report` |  | The local index must have mode 0600. |
| `index_read_only` | `report` |  | The search index file cannot be written. Reading it still answers when the file itself can be read. Give this user write access to the file, then build again. |
| `index_reset_refused` | `report` |  | The index pointer is not a file, so a new index cannot be published. Move it out of .forest/. |
| `index_schema_mismatch` | `run` | `memory-forest index <root>` | The local index schema is unsupported; rebuild the index. |
| `index_stale` | `run` | `memory-forest index <root>` | The local index no longer matches the canonical forest; rebuild the index. |
| `index_unreadable` | `run` | `forest index reset` | The index or its pointer is damaged, or the file is not a database. |
| `intake_adapter_unknown` | `fix` |  | No adapter is named …. The adapters are: …. |
| `intake_adapter_unset` | `fix` |  | Name the adapter with --from. |
| `intake_agent_invalid` | `fix` |  | … is not a slug. |
| `intake_agent_unset` | `fix` |  | Intake does not know which agent these turns belong to. Pass --as <id>, or set agent under [intake.tools.…]. |
| `intake_arguments` | `fix` |  | The word after `intake` is `status`, `reread` or `rename-agent`. |
| `intake_origin_invalid` | `fix` |  | That origin is not one the lane records. |
| `intake_path_unset` | `fix` |  | The jsonl adapter needs the folder of record files. Set path under [intake.tools.jsonl]. |
| `intake_rename_exists` | `fix` |  | … already has a lane or ids of its own. |
| `intake_role_invalid` | `fix` |  | That role is not one the lane records. |
| `intake_session_invalid` | `fix` |  | A stage session id cannot be a path. |
| `intake_text_empty` | `fix` |  | A turn needs text. |
| `intake_turn_rejected` | `fix` |  | The turn was not a record the lane keeps. |
| `internal_error` | `report` |  | The command failed without exposing internal details. |
| `interrupted` | `retry` |  | The operation was interrupted. |
| `into_missing` | `fix` |  | An area tree is retired into the page that took its rules (--into). |
| `invalid_arguments` | `fix` |  | The command arguments are invalid. |
| `invalid_config` | `fix` |  | The forest configuration is not valid JSON. |
| `invalid_daily_date` | `fix` |  | Daily filenames must use a valid YYYY-MM-DD date. |
| `invalid_daily_machine_block` | `fix` |  | A Daily entry marker exists outside a valid provenance-bound block. |
| `invalid_daily_path` | `fix` |  | Daily sources must be root-level ISO-date Markdown files. |
| `invalid_daily_plan` | `fix` |  | Unsupported Daily plan schema. |
| `invalid_document_path` | `fix` |  | Document paths must use forward slashes. |
| `invalid_istm_path` | `fix` |  | A raw lane is `06 istm/<file>.jsonl` or `06 istm/<agent>/<file>.jsonl`. |
| `invalid_jsonl` | `fix` |  | An ISTM event line is not valid JSON. |
| `invalid_jsonl_event` | `fix` |  | Each ISTM event line must be a JSON object. |
| `invalid_life_archive_path` | `fix` |  | Life Archive records may use at most domain/branch/leaf depth. |
| `invalid_limit` | `fix` |  | The result limit is outside the permitted range. |
| `invalid_lock_name` | `fix` |  | A lock name is a slug. |
| `invalid_ltm_path` | `fix` |  | LTM domains must use 02 ltm/<domain>_LTM.md. |
| `invalid_mtm_path` | `fix` |  | MTM branches must use 03 mtm/<domain>/<branch>.md. |
| `invalid_path` | `fix` |  | A state path must lie under .forest/. |
| `invalid_promotion_plan` | `fix` |  | Unsupported promotion plan schema. |
| `invalid_query_plan` | `fix` |  | A QueryPlan must contain only schema_version and probes. |
| `invalid_query_plan_probe` | `fix` |  | Each QueryPlan probe must contain only a query string. |
| `invalid_receipt` | `fix` |  | The transaction receipt must be a JSON object. |
| `invalid_retrieval_config` | `fix` |  | The optional retrieval configuration must contain only query_plan. |
| `invalid_route_segment` | `fix` |  | Route segments must be non-empty and may not be dot segments. |
| `invalid_stm_path` | `fix` |  | STM leaves must use 04 stm/<domain>/<branch>/<leaf>.md. |
| `invalid_structured_sweep_plan` | `fix` |  | ….layer must be xltm, ltm, mtm, or stm. |
| `invalid_transaction_state` | `report` |  | The write transaction cannot be prepared twice. |
| `invalid_utf8` | `fix` |  | Memory documents must contain valid UTF-8 text. |
| `invalid_write_operation` | `fix` |  | The writer operation is unsupported. |
| `invalid_write_plan` | `fix` |  | … must contain unique identifiers. |
| `invalid_xltm_path` | `fix` |  | XLTM must use the canonical root path 01 xltm/XLTM.md. |
| `journal_invalid` | `report` |  | The commit journal is not one this version wrote. |
| `kind_invalid` | `fix` |  | The kind is not one of the contract's leaf kinds. |
| `lane_fix_lines` | `report` |  | A rewritten lane lost or gained a line. |
| `layer_number_mismatch` | `fix` |  | The layer number and name do not match. |
| `leaf_name_clash` | `fix` |  | Leaf names of the folded tree would clash in its new folder. |
| `leaf_name_invalid` | `fix` |  | The leaf name cannot be a page name. |
| `link_count_exceeded` | `report` |  | The forest exceeds the configured wikilink limit. |
| `lint_code_invalid` | `fix` |  | A code keeps its class. |
| `lint_code_unknown` | `fix` |  | No such lint code. |
| `lock_already_held` | `retry` |  | This process already holds the lock; pass its token to re-enter it. |
| `lock_busy` | `retry` |  | Another writer holds the lock. |
| `lock_release_failed` | `report` |  | The maintenance lock could not be released safely. |
| `lock_token_invalid` | `report` |  | The token passed in names no live hold of this lock. |
| `lock_unsupported` | `report` |  | Forest locks need a POSIX system. |
| `log_agent_unset` | `fix` |  | Name the agent with --as, or the group and the caller with --as-group and --agent. |
| `log_caller_fields` | `fix` |  | The command sets the role and the origin. A caller cannot. Leave out --role and --origin. |
| `log_read_only` | `report` |  | This account cannot write the forest's .forest folder or the agent's raw lane, so the turn was not written. An account that can write the forest keeps it with `forest log --as …`, the turn on stdin. |
| `log_text_from_stdin` | `fix` |  | The turn is read from stdin, never from an argument. An argument would show in the process list. Write the turn on stdin of `forest log --as <agent>`. |
| `maintenance_lock_busy` | `retry` |  | The sibling maintenance lock is already held. |
| `mcp_agent_unset` | `fix` |  | Name the agent with --as. The reader id comes from the connection, not from a tool argument. |
| `mcp_bind` | `fix` |  | --http is PORT or HOST:PORT. |
| `mcp_group_invalid` | `fix` |  | Name the group with --as-group. The group id is a slug. |
| `mcp_reader_conflict` | `fix` |  | Pass --as or --as-group, not both. A tool argument cannot change which one it is. |
| `mcp_token_invalid` | `report` |  | The MCP token file must hold one line. |
| `mcp_token_missing` | `report` |  | The MCP server needs .forest/mcp/token, a regular file in mode 0600. |
| `mcp_token_mode` | `report` |  | The MCP token file must be mode 0600. Its contents were not shown. |
| `merge_collection` | `fix` |  | A collection is not merged; its entries are admitted or demoted. |
| `merge_into_smaller` | `fix` |  | Merge the leaf subject into the branch one, or promote it first. |
| `merge_same` | `fix` |  | A subject is not merged into itself. |
| `merge_tree_unsupported` | `fix` |  | A tree is folded first (`fold-tree`), then merged. |
| `missing_daily_source` | `fix` |  | A Structured disposition source is absent from canonical Daily. |
| `missing_parent` | `fix` |  | The forest root requires an existing real parent directory. |
| `missing_plan_input` | `fix` |  | Standard input was selected but no binary input stream was provided. |
| `missing_write_parent` | `report` |  | A required canonical writer parent is missing. |
| `move_record_invalid` | `fix` |  | An operation name is a lower-case word. |
| `move_to_same_place` | `fix` |  | The branch is already there. |
| `name_invalid` | `fix` |  | The … cannot stand in a registry line (it holds ·, ; or a backtick). |
| `name_missing` | `fix` |  | The … must not be empty. |
| `name_taken` | `fix` |  | Another subject or entry already holds one of these names. |
| `no_forest` | `run` | `forest init my-forest` | No forest.toml in the folder or any folder above it. |
| `non_regular_file` | `report` |  | Memory documents must be regular files. |
| `noncanonical_layer_directory` | `fix` |  | Layer directories must use the canonical space-separated name. |
| `noncanonical_route_segment` | `fix` |  | Route segments must use NFC Unicode normalization. |
| `noncanonical_write_path` | `fix` |  | Structured targets must resolve to exact canonical paths. |
| `not_a_collection_page` | `fix` |  | The page is not a collection page of the forest. |
| `not_a_leaf` | `fix` |  | Only a leaf moves this way (a collection page is cut with `forest collections`). |
| `not_a_leaf_path` | `fix` |  | A leaf goes to `04 stm/<tree>/<branch>/<leaf>.md`. |
| `not_a_slug` | `fix` |  | The entry's id cannot name a leaf. |
| `not_an_agent_tree` | `fix` |  | Only an agent's tree is filled from its settings entry. |
| `not_initialized` | `run` | `memory-forest init <root>` | The forest configuration is missing or not private. |
| `numbers_page_changed` | `retry` |  | The page changed while it was archived; not written. |
| `numbers_path_invalid` | `fix` |  | Only pages of the four forest layers are starred. |
| `numbers_star_incomplete` | `report` |  | A number would still stand in full; not written. |
| `operation_unknown` | `fix` |  | The collections tool has no such action. |
| `orchestrator_outside_group` | `fix` |  | The name is an orchestrator that is not a member of the group. |
| `out_of_scope` | `fix` |  | That path is outside this reader's trees and lane. |
| `page_changed_meanwhile` | `retry` |  | A page changed while the pass read it; nothing of it written. |
| `page_is_home` | `fix` |  | The page is a subject's registry home; move or merge the subject first. |
| `page_missing` | `fix` |  | There is no such page in the forest. |
| `page_removed` | `fix` |  | This page is removed earlier in the plan. |
| `path_escape` | `fix` |  | The requested path escapes the selected forest root. |
| `path_has_no_parent` | `fix` |  | This page has no parent page. |
| `path_not_found` | `fix` |  | The requested path does not exist. |
| `path_not_in_forest` | `fix` |  | Only pages of the four forest layers are written. |
| `plan_changed` | `retry` |  | The write plan changed while it was being read. |
| `plan_check_failed` | `report` |  | The planned change would break the forest; nothing was written. |
| `plan_file_invalid` | `fix` |  | A plan file is a list of operations (or an object with `operations`). |
| `plan_not_found` | `fix` |  | The selected write plan does not exist. |
| `plan_too_large` | `fix` |  | The write plan exceeds the supported byte limit. |
| `plugin_failed` | `report` |  | A plugin ended the process. |
| `postwrite_audit_failed` | `report` |  | The canonical write failed the adjacent-link audit. |
| `postwrite_validation_failed` | `report` |  | The canonical write failed structural validation. |
| `preflight_audit_failed` | `report` |  | Canonical writes require a forest that already passes audit. |
| `preflight_validation_failed` | `report` |  | Canonical writes require a forest that already validates. |
| `promote_collection` | `fix` |  | A collection is not promoted; its entries are admitted one by one. |
| `query_failed` | `report` |  | The local SQLite FTS5 index could not be queried. |
| `query_plan_changed` | `retry` |  | The QueryPlan source changed while it was being opened. |
| `query_plan_input_unavailable` | `fix` |  | The QueryPlan source '-' requires a binary standard-input stream. |
| `query_plan_not_found` | `fix` |  | The selected QueryPlan file does not exist. |
| `query_plan_open_failed` | `report` |  | The QueryPlan file could not be opened safely. |
| `query_plan_too_large` | `fix` |  | The QueryPlan exceeds the 32768-byte limit. |
| `query_too_long` | `fix` |  | The query exceeds the 1000-character limit. |
| `read_failed` | `report` |  | A file could not be read as text. |
| `read_lines` | `fix` |  | Lines are written A-B, for example 4-12. |
| `read_lines_empty` | `fix` |  | Those lines fall outside the addressed section. |
| `reader_conflict` | `fix` |  | Pass --as or --as-group, not both. |
| `reason_missing` | `fix` |  | An archived page needs a reason. |
| `receipt_capacity_exceeded` | `report` |  | The private receipt store is full; archive receipts before writing. |
| `receipt_collision` | `retry` |  | A receipt appeared during the transaction. |
| `receipt_conflict` | `report` |  | The existing receipt does not bind this exact write plan. |
| `receipt_count_exceeded` | `report` |  | The receipts directory exceeds the supported entry limit. |
| `receipt_state_mismatch` | `report` |  | The receipt exists but the canonical Daily transaction is missing. |
| `receipt_too_large` | `report` |  | The deterministic receipt exceeds its supported byte limit. |
| `receipt_write_failed` | `report` |  | The transaction receipt could not be created safely. |
| `record_missing` | `fix` |  | The page has no update record. |
| `record_page_changed` | `retry` |  | The page changed while it was read; nothing was written. |
| `record_path_invalid` | `fix` |  | Only pages of the four forest layers have a record to trim. |
| `record_trim_unsafe` | `report` |  | A trim would change the page outside its update record; nothing was written. |
| `registry_invalid` | `run` | `forest registry build` | The registry file is not a JSON object; rebuild it. |
| `registry_missing` | `run` | `forest registry build` | The forest has no compiled registry; run `forest registry build`. |
| `rename_collection` | `fix` |  | A collection's id follows its category; rename the category branch instead. |
| `rename_nothing` | `fix` |  | Give a new id, a new name, or both. |
| `rename_to_same` | `fix` |  | The branch already has that name. |
| `rename_tree_unsupported` | `fix` |  | A tree's id names its pages and folders; fold it or keep its id. |
| `repeats_removal_unsafe` | `report` |  | A removal would change more than the repeated lines. |
| `role_set` | `fix` |  | The tree page's role is set already; a set value is changed by hand. |
| `rollback_failed` | `report` |  | The failed write could not be fully rolled back. |
| `root_exists` | `fix` |  | Initialization requires a new path and never writes into an existing one. |
| `root_not_directory` | `fix` |  | The selected forest root is not a directory. |
| `root_not_empty` | `fix` |  | forest init needs a new or empty folder. |
| `root_not_found` | `fix` |  | The selected forest root does not exist. |
| `root_not_owned` | `report` |  | The selected forest root must be owned by the current user. |
| `scan_failed` | `report` |  | A forest directory could not be scanned. |
| `search_empty` | `fix` |  | Search needs the words to look for. |
| `search_limit` | `fix` |  | The limit is at least 1. |
| `settings_unpatched` | `report` |  | The group's members line could not be updated. The owner writes members as a list of slugs under the group. |
| `standing_invalid` | `fix` |  | A standing line is `said — "<words>" (YYYY-MM-DD)` or `own — "<words>"`. |
| `state_permissions` | `report` |  | The derived-state directory must exist with mode 0700. |
| `stream_empty` | `fix` |  | Name the pages of one stream. |
| `stream_fields_differ` | `fix` |  | The pages differ in a field a re-cut cannot merge. |
| `stream_in_several_folders` | `fix` |  | A stream's pages share one folder. |
| `stream_kinds_differ` | `fix` |  | The pages of one stream share one kind. |
| `stream_leaf_empty` | `fix` |  | The page holds no dated line. |
| `stream_leaf_not_a_list` | `fix` |  | A stream page holds only its title, a description, Timeline, Links and Update record; anything else is cut by sub-topic. |
| `stream_names_differ` | `fix` |  | Once the period is taken off, the pages of one stream share one name. |
| `stream_other_subject` | `fix` |  | The page is not a leaf of this subject. |
| `stream_page_taken` | `fix` |  | A page of the new cut exists and is not a page of this stream. |
| `structured_preimage_mismatch` | `fix` |  | A Structured create target already exists. |
| `structured_snapshot_invalid` | `report` |  | The current Structured forest must validate before it can be bound. |
| `structured_snapshot_mismatch` | `retry` |  | The Structured forest changed after model review. |
| `subject_has_no_leaf` | `fix` |  | No leaf of this subject was found. |
| `subject_unknown` | `fix` |  | The … names no registered subject or collection entry. |
| `subtopic_invalid` | `fix` |  | A leaf's sub-topic cannot name a branch. |
| `successor_missing` | `fix` |  | The successor page is not in the forest. |
| `symlink_forbidden` | `report` |  | Symlinks are not allowed inside a Memory Forest. |
| `takeover_collection_conflict` | `fix` |  | Both the category and the shared branch keep a collection. |
| `target_exists` | `fix` |  | A leaf of this id already exists. |
| `time_phrase_empty` | `fix` |  | The time phrase is empty. |
| `time_phrase_unknown` | `fix` |  | A phrase row names a use this matcher does not know. |
| `tool_failed` | `report` |  | The tool could not answer. |
| `transaction_conflict` | `fix` |  | The Daily transaction identifier already exists with different bytes. |
| `transcript_deferred` | `report` |  | The hook left this session for a sweep. |
| `transcript_missing` | `report` |  | The transcript root is not a directory. |
| `tree_exists` | `fix` |  | A tree of this id already exists. |
| `tree_not_empty` | `fix` |  | The tree still holds boards, leaves or subjects; move or merge them first. |
| `tree_type_not_allowed` | `fix` |  | Only a subject whose type is in levels.tree_types becomes a tree. |
| `tree_unknown` | `fix` |  | The tree has no page or index; make it with new-tree first. |
| `type_collection` | `fix` |  | A collection is a subject of its own kind; use `forest collections`. |
| `type_unchanged` | `fix` |  | The subject already has this type. |
| `type_unknown` | `fix` |  | The type is not one of the forest's subject types. |
| `unjournaled_parent_directory` | `report` |  | A required parent directory was not captured in the write journal. |
| `unknown_command` | `fix` |  | The requested command is not supported. |
| `unknown_layer` | `fix` |  | The first path segment is not a supported Memory Forest layer. |
| `unsafe_daily_directory` | `report` |  | A required directory must be real and mode 0700. |
| `unsafe_daily_entry` | `report` |  | Canonical Daily may contain only regular Markdown files. |
| `unsafe_existing_path` | `report` |  | An expected directory exists as a symlink or non-directory. |
| `unsafe_index_path` | `report` |  | The index path must be a regular file or not exist. |
| `unsafe_index_staging` | `report` |  | The index staging directory must be a real private sibling on the same filesystem. |
| `unsafe_lock_parent` | `report` |  | The maintenance lock parent must be a real directory. |
| `unsafe_mutation_target` | `report` |  | Canonical write targets must be real private files. |
| `unsafe_parent_directory` | `report` |  | Writer parent directories must be real and private. |
| `unsafe_plan_permissions` | `report` |  | The write plan must be owned by the current user. |
| `unsafe_plan_source` | `report` |  | The plan path must have no symlink components. |
| `unsafe_query_plan_source` | `report` |  | The QueryPlan source must be a regular non-symlink file. |
| `unsafe_receipt_entry` | `report` |  | Receipt entries must be bounded private transaction JSON files. |
| `unsafe_receipts_directory` | `report` |  | The receipts directory must be a real private directory. |
| `unsafe_root` | `retry` |  | The forest root changed while it was being inspected. |
| `unsafe_state_directory` | `report` |  | The derived-state path must be a real directory. |
| `unsafe_state_entry` | `report` |  | Derived state may contain only known files and receipts. |
| `unsafe_transcript` | `fix` |  | A transcript path cannot contain '..'. |
| `unsafe_write_journal` | `report` |  | The write journal must be a real private directory. |
| `unsupported_file_type` | `fix` |  | Canonical memory documents must use .md, except ISTM .jsonl streams. |
| `unsupported_layer` | `fix` |  | The memory layer is not supported by this schema. |
| `unsupported_query_plan_schema` | `fix` |  | The QueryPlan schema version is unsupported. |
| `write_journal_exists` | `run` | `forest recover` | An earlier write journal must be recovered before a new transaction. |
| `write_journal_failed` | `report` |  | A durable write-journal file could not be created. |
| `write_readback_failed` | `report` |  | A removed page is still there. |
| `write_recovery_failed` | `report` |  | The interrupted write could not be fully recovered. |
| `write_target_escape` | `fix` |  | A write transaction target escapes the selected forest. |
| `write_transaction_too_large` | `fix` |  | The write transaction exceeds its target bound. |
