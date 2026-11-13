def median(values):
    if not values:
        raise ValueError("empty")
    ordered = sorted(values)
    mid = len(ordered) // 2
    # Bug: even lengths should average the two middle values.
    return ordered[mid]
