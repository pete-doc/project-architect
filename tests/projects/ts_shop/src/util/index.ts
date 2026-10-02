export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
