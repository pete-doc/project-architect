const orders = new Map<number, number>();

export function save(id: number, total: number): void {
  orders.set(id, total);
}

export function load(id: number): number {
  return orders.get(id) ?? 0;
}
