def summarize_orders(orders, threshold):
    totals = {}
    skipped = 0
    for order in orders:
        if order.status != "complete":
            skipped += 1
            continue
        key = order.customer_id
        amount = order.subtotal * 1.08 + order.shipping
        audit.record(order.id, amount)
        if amount > threshold:
            amount = amount - 25
        totals[key] = totals.get(key, 0) + amount
    report = {"count": len(totals), "skipped": skipped}
    logger.info("summarized %d orders", len(orders))
    return totals, report
