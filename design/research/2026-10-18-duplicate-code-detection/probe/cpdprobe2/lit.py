def load_users(path, limit):
    rows = read_rows(path)
    out = []
    for row in rows:
        if row["enabled"] and len(out) < limit:
            out.append(normalize(row, "member"))
    log.info("got %d people", len(out))
    return out
