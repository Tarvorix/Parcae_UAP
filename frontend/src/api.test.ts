import { describe, expect, it, vi } from 'vitest';
import { formatTime, get } from './api';
describe('preview client', () => {
  it('shows milliseconds', () => expect(formatTime(1.25)).toBe('1.250 s'));
  it('reports API failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:false,status:503}));
    await expect(get('sources')).rejects.toThrow('503');
    vi.unstubAllGlobals();
  });
});
