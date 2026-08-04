# Retrieval evaluation dataset sources

The checked-in `retrieval-daily-v1` corpus contains synthetic metadata records created specifically
for CommerceVision Agent's deterministic CI evaluation. It contains no third-party image binary,
personal data, product claim, trademark artwork, or scraped payload.

The beauty records model lipstick package references. The automotive-accessory records model roof
rack references. Their synthetic descriptors and identifiers are dedicated to this repository and
released as CC0-1.0 evaluation fixtures.

Every manifest asset carries an explicit Rights Record identity, version, source-document path, and
license. Each query freezes the Rights decision for the complete candidate universe under its exact
purpose and provider. Negative cross-category candidates deliberately remain in the universe to
prove that unauthorized recall stays zero without retaining their payload in reports.

Hidden release data is not stored in this public daily-tuning directory. Operators provide it as a
separate `hidden-release` manifest and observations bundle when running the release profile.
