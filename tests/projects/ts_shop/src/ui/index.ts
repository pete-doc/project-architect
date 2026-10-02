import { totalOf } from '../orders';

export function show(id: number): string {
  return `Order ${id}: ${totalOf(id).toFixed(2)}`;
}
