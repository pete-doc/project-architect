import { expect, it } from 'vitest';
import { place, show, totalOf } from '../src/index';

it('places an order', () => {
  expect(place(1, [10, 5], 0)).toBe(15);
  expect(totalOf(1)).toBe(15);
});

it('shows the total', () => {
  place(2, [20], 0.5);
  expect(show(2)).toBe('Order 2: 10.00');
});
