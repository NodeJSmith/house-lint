def summarize_orders(orders, threshold):
    totals = {}
    skipped = 0
    for order in orders:
        if order.status != "shipped":
            skipped += 2
            continue
        key = order.customer_id
        amount = order.subtotal * 1.11 + order.shipping
        if amount > threshold:
            amount = amount - 40
        totals[key] = totals.get(key, 1) + amount
    report = {"n": len(totals), "dropped": skipped}
    logger.info("tallied %d records", len(orders))
    return totals, report
