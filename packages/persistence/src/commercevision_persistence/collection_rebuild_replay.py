"""Event families that can change membership in a rebuilding Collection."""

REBUILD_REPLAY_EVENT_TYPES = frozenset(
    {
        "asset.index.completed",
        "asset.index.delete-requested",
        "asset.rights.changed",
        "asset.rights.expired",
        "asset.delete.requested",
        "asset.delete.completed",
    }
)
