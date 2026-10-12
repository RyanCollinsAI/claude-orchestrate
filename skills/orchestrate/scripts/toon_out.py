import toon_format


def to_toon(rows, fields):
    """Serialize a list of dicts to TOON format, selecting only specified fields.

    Args:
        rows: List of dicts to serialize
        fields: List of field names to include

    Returns:
        TOON format string
    """
    if not rows:
        return toon_format.dumps([])

    filtered = []
    for row in rows:
        filtered_row = {field: row.get(field) for field in fields}
        filtered.append(filtered_row)

    return toon_format.dumps(filtered)


def count_line(n, total):
    """Format a count line showing progress.

    Args:
        n: Current count
        total: Total count

    Returns:
        Formatted string like "count: 5 of 10 total"
    """
    return f"count: {n} of {total} total"
