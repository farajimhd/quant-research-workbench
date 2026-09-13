/** Keep causal references inside their recorded time span, even for tiny trades. */
export function tradeGuideSpan(left: number, right: number, width: number, causal: boolean) {
  const rawLeft = Math.min(left, right);
  const rawRight = Math.max(left, right);
  let renderedLeft = Math.max(0, rawLeft);
  let renderedRight = Math.min(width, rawRight);
  const minimumWidth = Math.min(56, width);
  if (!causal && renderedRight - renderedLeft < minimumWidth) {
    const center = Math.max(0, Math.min(width, (rawLeft + rawRight) / 2));
    renderedLeft = Math.max(0, Math.min(width - minimumWidth, center - minimumWidth / 2));
    renderedRight = Math.min(width, renderedLeft + minimumWidth);
  }
  return { left: renderedLeft, right: renderedRight };
}
/** Freeze the recorded entry values; later reference changes cannot rewrite a position. */
export function positionReferenceSegments<T extends { start: number; end: number }>(
  references: readonly T[],
  positions: readonly { entryTime: number; endTime?: number; exitTime?: number }[],
): T[] {
  const asOf = references.reduce((latest, reference) => Math.max(latest, reference.end), -Infinity);
  return positions.flatMap(position => {
    const reference = references.reduce<T | undefined>((selected, candidate) =>
      candidate.start <= position.entryTime && candidate.end > position.entryTime
        && (!selected || candidate.start > selected.start) ? candidate : selected, undefined);
    if (!reference) return [];
    const start = position.entryTime;
    const end = Math.min(asOf, position.exitTime ?? position.endTime ?? position.entryTime);
    return Number.isFinite(start) && Number.isFinite(end) && end > start
      ? [{ ...reference, start, end }] : [];
  });
}
