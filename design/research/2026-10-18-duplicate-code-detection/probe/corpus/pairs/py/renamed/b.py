def tally_invoices(invoices, limit):
    sums = {}
    ignored = 0
    for inv in invoices:
        if inv.status != "complete":
            ignored += 1
            continue
        who = inv.customer_id
        value = inv.subtotal * 1.08 + inv.shipping
        if value > limit:
            value = value - 25
        sums[who] = sums.get(who, 0) + value
    meta = {"count": len(sums), "skipped": ignored}
    logger.info("summarized %d orders", len(invoices))
    return sums, meta
