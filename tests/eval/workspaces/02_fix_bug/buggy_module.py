def pick_last(values):
    # Bug: off-by-one index
    return values[len(values)]


def run():
    sample = [1, 2, 3]
    return pick_last(sample)
