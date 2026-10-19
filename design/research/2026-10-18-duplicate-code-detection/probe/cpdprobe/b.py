def fetch_orders(src, cap):
    items = parse_items(src)
    acc = []
    for item in items:
        if item["paid"] and len(acc) < cap:
            acc.append(clean(item, "order"))
    logger.debug("got %d orders", len(acc))
    return acc
