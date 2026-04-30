import { describe, it, expect, afterEach, vi } from 'vitest'

describe('API_BASE', () => {
    afterEach(() => {
        vi.unstubAllEnvs()
        vi.resetModules()
    })

    it('reads VITE_API_BASE from env when set', async () => {
        vi.stubEnv('VITE_API_BASE', 'https://pdf.printosky.com')
        const mod = await import('./config')
        expect(mod.API_BASE).toBe('https://pdf.printosky.com')
    })

    it('falls back to local dev URL when VITE_API_BASE is missing or empty', async () => {
        vi.stubEnv('VITE_API_BASE', '')
        const mod = await import('./config')
        expect(mod.API_BASE).toBe('http://127.0.0.1:8000')
    })

    it('strips a trailing slash so callers can safely concatenate paths', async () => {
        vi.stubEnv('VITE_API_BASE', 'https://pdf.printosky.com/')
        const mod = await import('./config')
        expect(mod.API_BASE).toBe('https://pdf.printosky.com')
    })
})
