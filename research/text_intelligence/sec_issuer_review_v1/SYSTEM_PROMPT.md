# SEC issuer review prompt V1

The runtime prompt is the `SYSTEM_PROMPT` constant in `prompt.py`. It requires
the remote reviewer to use only the persisted SEC Synthesis record, preserve
the filing acceptance-time boundary, cite exact deterministic evidence IDs,
respect unresolved XBRL comparability, and abstain on insufficient or materially
conflicting evidence. The remote output cannot revise deterministic synthesis;
it is stored as a separate manual review authority.
