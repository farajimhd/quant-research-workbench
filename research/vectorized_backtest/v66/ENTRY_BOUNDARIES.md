# Inherited reference

This document is retained from V64 for design provenance. Historical performance
and qualification statements below are not V66 results. See README.md for V66
input, initialization, and feature changes; workstation evidence lives in its runtime.

# V64 price ownership

This version supersedes V62 bounded-swing fallback. Sampling, crossover and
mutation pass candidate-owned explicit price programs through one authority.
Static type and parameter constraints are checked at creation. Dynamic reference
availability and stop/buy/target relationships are guarded in the strategy.
Execution accepts concrete requests and records invalid/blocked outcomes.

Candidate abstentions are reported separately from submitted requests. A missing
swing is never silently replaced unless the genome explicitly contains an
`available` expression selecting an alternate reference. Execution never clamps
or invents the requested stop. Its only mandatory decision is the agreed
rank-loss/session-end liquidation lifecycle.
