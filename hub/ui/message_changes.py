"""Shared immutable-message comparison for presentation and render caches."""


def unchanged_prefix(previous, current):
    """Find the reusable prefix using tuple comparisons, not per-row callbacks.

    Stable objects compare by identity inside CPython's tuple loop. Equal
    detached observations are reusable too. A deletion/reorder/replacement is
    reconciled from its first changed row; no producer revision is trusted.
    """
    if previous is current:
        return len(current)
    limit = min(len(previous), len(current))
    block = 64
    for start in range(0, limit, block):
        stop = min(start + block, limit)
        if previous[start:stop] != current[start:stop]:
            return next(index for index in range(start, stop)
                        if previous[index] != current[index])
    return limit
