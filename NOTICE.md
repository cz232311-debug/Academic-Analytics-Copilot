# Upstream attribution and contribution boundary

Academic Analytics Copilot was proposed after reviewing **Student Performance Analyzer** by **Chirag Khatri (CH-Anonymous)**:

- Repository: [CH-Anonymous/student-performance-analyzer](https://github.com/CH-Anonymous/student-performance-analyzer)
- Reviewed commit: `c691eff6ecc0de351ffcdf26bdce436e5daa64e2`
- Upstream copyright: Copyright (c) 2025 Chirag Khatri
- Upstream license: MIT; the complete unmodified notice is in `third_party/student-performance-analyzer/LICENSE`.

The original project is a Streamlit dashboard with descriptive visualizations and linear regression. Its author did not implement the proposed Copilot backend, normalized synthetic data, controlled LLM tools, numerical evidence validation or evaluation system.

This repository currently contributes an upstream audit, reproducible audit scripts, collected evidence, an independent implementation proposal and an English evaluation specification. Product implementation remains pending. Upstream source is held locally in an ignored audit directory for review; upstream CSV, pickle model and screenshots are not part of the proposed application. The included upstream LICENSE is a provenance copy and does not reassign authorship of new work to the upstream author.

Dependency licenses remain those of their respective owners. Files under `docs/audit/evidence/dependency-licenses/` are unmodified notices extracted from the specific installed wheels, not project-authored license texts. See the dependency inventory and audit limitations before redistribution.
