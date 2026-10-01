# 0001. Symbol-level, incremental indexing in one collection per workspace

## Context

CodeIndex must return the smallest useful piece of code for an error, to keep token use low. It must also stay cheap to refresh, because it runs on every change of a working repository.

## Decision

- **Granularity: symbols.** The indexed unit is a module, class, function or method, not a file or a fixed-size chunk. Nested functions stay inside their parent. Each module gets its own record holding its docstring and imports, so dependencies are available without a separate store.
- **Incremental by file hash.** Every record carries the SHA-256 of its file. `index` re-parses only files whose hash changed and removes files that disappeared.
- **One collection per workspace.** The collection name is derived from the resolved workspace path, because relative file paths are not unique across projects.
- **Full symbol stored as JSON in metadata.** Flat fields (file, kind, name, lines) allow filtering; a JSON payload rebuilds the `Symbol` on read. The embedded document is a short text (names, signature, docstring, start of the body) because the embedding model truncates long input.
- **Error-tolerant parsing.** Files with syntax errors are indexed with the definitions Tree-sitter could recognise and are reported in the summary.

## Consequences

- Retrieval returns exact line ranges that a patch can target.
- Source is stored twice (document and JSON payload), capped at 20,000 characters per symbol; the `truncated` flag marks capped symbols.
- Moving a workspace to another path creates a new collection; the old one is not cleaned up automatically.
- `index` reads every file to hash it. This is fast for repositories of normal size; a modification-time shortcut can be added if it ever matters.
- Only Python is supported. Another language needs its own `CodeParser` adapter.
