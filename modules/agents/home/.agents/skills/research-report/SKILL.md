---
name: research-report
description: Research a question across sources and produce an evidence-backed report, comparison, or literature synthesis using the active runtime's search and retrieval tools. Use for substantial research requests; answer brief factual lookups directly.
---

# Research reports

This is an owned workflow for native search and retrieval. It does not invoke
or reproduce the account-synced Claude deep-research skill.

Establish the question, audience, date range, geography, and requested output.
Resolve an ambiguity only when it changes the research. Use the user's sources
and connected tools where relevant; do not infer access from an installed skill
or plugin name. If the required source is inaccessible, identify it and limit
the claim accordingly. Search results are leads: read the underlying sources.

Build a small coverage plan around the decisions the report must support.
Prefer primary evidence and current official documentation for technical work.
Record publication date separately from the date an event happened. For each
material claim, keep the source URL, date accessed, supporting evidence, and
any uncertainty or conflicting result. Distinguish a source's statement from
your inference. Follow the runtime's citation and quotation limits.

Work directly unless delegation is authorized and useful. If using workers,
follow the runtime's orchestration rules and live schemas. Give each a bounded
question, source access, date constraints, expected evidence, and its own output
path if files were requested. No named Claude agent is required. A worker with
no search access cannot substitute invented citations. Do not use CLI proxies
or top-level threads as substitutes for delegated workers.

Read the returned evidence before synthesis, check the central claims against
the sources, and resolve contradictions where possible. Stop expanding the
search when the question is answered to the requested depth; report unresolved
gaps rather than starting an indefinite research loop.

Lead the report with its supported answer. Explain decisive evidence, relevant
alternatives, and practical limits. Attach citations close to the claims they
support. Report a time-sensitive conclusion with its as-of date. Keep facts,
inferences, and recommendations distinguishable.

Honor the requested destination and file format. A report requested in chat
stays in chat. Use the runtime's document/page skill if the user asks to save a
collaborative document, or its format-specific skill for a named file type.
Preserve prior reports when writing an update and include their provenance.
