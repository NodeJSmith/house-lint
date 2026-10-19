export function tallyInvoices(invoices: Order[], limit: number): [Map<string, number>, Report] {
  const sums = new Map<string, number>();
  let ignored = 0;
  for (const inv of invoices) {
    if (inv.status !== "complete") {
      ignored += 1;
      continue;
    }
    const who = inv.customerId;
    let value = inv.subtotal * 1.08 + inv.shipping;
    if (value > limit) {
      value = value - 25;
    }
    sums.set(who, (sums.get(who) ?? 0) + value);
  }
  const meta = { count: sums.size, skipped: ignored };
  logger.info("summarized %d orders", invoices.length);
  return [sums, meta];
}
