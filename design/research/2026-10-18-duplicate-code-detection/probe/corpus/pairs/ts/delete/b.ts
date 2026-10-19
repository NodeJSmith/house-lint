export function summarizeOrders(orders: Order[], threshold: number): [Map<string, number>, Report] {
  const totals = new Map<string, number>();
  let skipped = 0;
  for (const order of orders) {
    if (order.status !== "complete") {
      skipped += 1;
      continue;
    }
    const key = order.customerId;
    let amount = order.subtotal * 1.08 + order.shipping;
    totals.set(key, (totals.get(key) ?? 0) + amount);
  }
  const report = { count: totals.size, skipped: skipped };
  logger.info("summarized %d orders", orders.length);
  return [totals, report];
}
