# Development and review records

From Phase 2B onward, each development round must:

- Record the starting Git commit and pre-existing working-tree changes before editing.
- Reuse the current architecture. Do not change business logic merely to generate reports.
- Implement independently verifiable increments, run relevant tests, fix failures, then commit each coherent increment. Do not combine unrelated changes or commit user-owned artifacts without authorization.
- Maintain `docs/development/02b-workbench.md` as the Phase 2B progress index. Add a per-round learning/review packet under `docs/development/`.
- Record every added, modified and deleted file with its responsibility, and distinguish implemented, actually verified and pending capabilities.
- Provide a cumulative diff from the starting commit through the implementation checkpoint, plus per-commit patches covering all related commits. Include working-tree changes separately and list untracked new source files explicitly. Declare the exact snapshot boundary; generated review artifacts cannot recursively include their own final commit.
- Record actual test commands, completed counts, failures/fixes and unverified behavior. Never present Mock success as live LLM verification. Do not run paid LLM calls without explicit authorization.
- Explain 2–3 important new code excerpts, their call chains and design reasons, and record problems and tradeoffs.
- Exclude secrets, `.env`, student records, databases and private logs from review packages. Include only source, configuration examples, documentation and sanitized test evidence. State which files the owner can share for independent review.
- Do not invent historical commits or claim reconstructed milestones were committed at their original execution time.

The user is learning application engineering. Provide clear Chinese learning explanations and English project documentation where appropriate.
