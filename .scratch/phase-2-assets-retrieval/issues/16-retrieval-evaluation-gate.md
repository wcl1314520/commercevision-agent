# 16 — Fixed retrieval evaluation and quality release gate

**What to build:** Add licensed beauty and automotive-accessory retrieval datasets, deterministic
evaluation tooling, statistical reports, and CI/release gates for relevance, latency, and rights
safety. Evaluation configuration is versioned data rather than hard-coded ranking logic.

**Blocked by:** 11 — Rights-first hybrid retrieval and Retrieval Explorer; 14 — Milvus rebuild and Collection upgrade.

**Status:** ready-for-agent

- [ ] Dataset manifests freeze query, category, candidate universe, relevance grades, rights snapshot, purpose, provider, split, and policy version.
- [ ] Demo and evaluation assets have explicit Rights Records and source documentation.
- [ ] Reports calculate Recall@5/10/20, Precision@5/10/20, MRR, nDCG with graded gain, P50, P95, and per-category/per-vector-kind breakdowns.
- [ ] Reports calculate UnauthorizedRecall@K, unauthorized return count, and queries with unauthorized results.
- [ ] All three unauthorized metrics must equal zero for CI and release acceptance.
- [ ] Relevance and latency thresholds live in a versioned manifest and report bootstrap 95% confidence intervals.
- [ ] Exact FLAT search provides the ANN recall reference for the fixed corpus.
- [ ] Hidden release data is separated from daily tuning data.
- [ ] Evaluation output is machine-readable, human-readable, reproducible by policy/model/collection version, and retained without unauthorized payloads.
- [ ] A deterministic small evaluation runs in CI and a full profile is available for release verification.

