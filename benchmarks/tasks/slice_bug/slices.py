def last_n(items, n):
    if n < 0:
        raise ValueError("n must be >= 0")
    if n == 0:
        return []
    # Bug: drops one element too many.
    return list(items[-n + 1 :])
