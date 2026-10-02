import { expect, it } from 'vitest';
import { applyDiscount } from '../src/pricing';

it('applies a discount', () => {
  expect(applyDiscount(100, 0.25)).toBe(75);
});

it('caps the discount at ninety percent', () => {
  expect(applyDiscount(100, 5)).toBe(10);
});
