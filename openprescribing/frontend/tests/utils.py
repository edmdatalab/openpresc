def round_floats(value, precision=9):
    """
    Round all floating point values found anywhere within the supplied data
    structure, recursing our way through any nested lists, tuples or dicts
    """
    if isinstance(value, float):
        return round(value, precision)
    elif isinstance(value, list):
        return [round_floats(i, precision) for i in value]
    elif isinstance(value, tuple):
        return tuple(round_floats(i, precision) for i in value)
    elif isinstance(value, dict):
        return {k: round_floats(v, precision) for (k, v) in value.items()}
    else:
        return value
