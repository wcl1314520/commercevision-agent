# Retrieval evaluation profiles

`daily-v1` is the small, deterministic validation suite committed for CI. It contains synthetic,
CC0 metadata fixtures for beauty and automotive-accessory retrieval and no third-party image binary.

The full release corpus is provisioned outside Git at `evaluation/retrieval/hidden-release/`. Its
manifest must declare `profile: release`, `split: hidden-release`, frozen policy/model/collection and
Rights identities, and `confidence-bound` thresholds. The loader rejects hidden data under the daily
profile and daily/validation data under the release profile.

Reports contain aggregate metrics and version identities only. They never retain candidate IDs,
Rights payloads, query text, object locations, preview references, or unauthorized content.
