def load_users(path, limit):
    records = read_records(path)
    result = []
    for rec in records:
        if rec["active"] and len(result) < limit:
            result.append(normalize(rec, "user"))
    log.info("loaded %d users", len(result))
    return result
