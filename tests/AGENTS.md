# Test instructions

- Prefer focused tests for the requested module before broader suites. Use representative fixtures and real contracts where practical.
- Keep generated test output, coverage, reports, caches, and temporary files outside the repository under the machine-specific runtime root.
- Validate the runnable path, not only isolated helpers. Report exactly what passed, what was not run, and any environment limitation.
- Do not weaken assertions or replace a required integration check with a workaround without explicit explanation.
- For parsers, renderers, normalizers, and converters, audit representative difficult source samples through original, intermediate, and final outputs, including length/distribution effects before choosing truncation or chunking.
