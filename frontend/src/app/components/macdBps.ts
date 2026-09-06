/** Use each observation's own candle close, never the latest price for history. */
export function macdBpsPoints<T extends { time: number; value: number }>(
  points: T[], candles: Array<{ time: number; close: number }>,
): T[] {
  const prices = new Map(candles.map((candle) => [candle.time, candle.close]));
  return points.flatMap((point) => {
    const price = prices.get(point.time);
    if (!Number.isFinite(point.value) || price === undefined || !Number.isFinite(price) || price <= 0) return [];
    return [{ ...point, value: point.value / price * 10_000 }];
  });
}
