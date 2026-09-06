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
