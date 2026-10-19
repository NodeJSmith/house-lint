def load_users(path, limit):
    rows = read_rows(path)
    out = []
    for row in rows:
        if not row:
            continue
        if row["active"] and len(out) < limit:
            out.append(normalize(row, "user"))
    log.info("loaded %d users", len(out))
    return out
