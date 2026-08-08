# GPT Pro final arbiter result for R8

## Verdict

`REPAIR_AUDIT_MORE_FIXES`

The arbiter independently confirmed the R8 ZIP anchors, 64/64 payload entries,
53/53 clean-root tests, two byte-identical 27-file trees, the completion-only
refresh, and all real-CLI parser/read failures. No model action occurred.

## Input-boundary repairs confirmed fixed

- 10,000-digit JSON integer in report, anchor, or master: exit 1, structured
  canonical FAIL, `aggregate=null`, no traceback.
- Malformed JSON, invalid UTF-8, non-object root, missing/unreadable input: the
  same bounded fail-closed result.
- R7 extreme numeric and R5 four-blocker regression attacks remained fixed.

## Remaining authorization blockers

1. A pre-existing regular output file is preserved, but the CLI raises an
   uncaught `ContractError` and emits a traceback instead of a bounded canonical
   FAIL result.
2. A pre-existing broken output symlink is missed by `Path.exists()`; the CLI
   follows it and creates the link target while returning PASS.

## Required narrow repair

Use lexical existence (`os.path.lexists`) to reject every pre-existing output
directory entry, including broken symlinks. Preserve it and any link target,
emit bounded canonical FAIL JSON to stdout, return exit 1, and never traceback.

`authorization_request_allowed=false`; `scientific_evidence=false`.
