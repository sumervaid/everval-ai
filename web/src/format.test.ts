import { describe, expect, it } from 'vitest';
import { money, parseObject, percent } from './format';

describe('evidence formatting', () => {
  it('distinguishes unknown values from measured zero', () => {
    expect(money(null)).toBe('—');
    expect(money(0)).toBe('$0.00');
    expect(percent(undefined)).toBe('—');
    expect(percent(0)).toBe('0.0%');
  });
  it('preserves sub-cent costs instead of rounding them to zero', () => {
    expect(money(0.000004)).toBe('$0.000004');
  });
  it('rejects arrays and primitives as scenario input objects', () => {
    expect(() => parseObject('[]', 'Inputs')).toThrow('Inputs must be a JSON object.');
    expect(() => parseObject('null', 'Inputs')).toThrow();
    expect(() => parseObject('42', 'Inputs')).toThrow();
    expect(parseObject('{"amount": 42}', 'Inputs')).toEqual({ amount: 42 });
  });
});
