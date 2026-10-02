import { clamp } from '../util';

export function applyDiscount(amount: number, discount: number): number {
  return Math.round(amount * (1 - clamp(discount, 0, 0.9)) * 100) / 100;
}
