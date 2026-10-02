import { load, save } from '../db';
import { applyDiscount } from '../pricing';

export function place(id: number, prices: number[], discount: number): number {
  const total = applyDiscount(
    prices.reduce((sum, price) => sum + price, 0),
    discount,
  );
  save(id, total);
  return total;
}

export function totalOf(id: number): number {
  return load(id);
}
