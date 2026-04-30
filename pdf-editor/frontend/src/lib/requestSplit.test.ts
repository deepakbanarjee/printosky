import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { requestSplit } from './requestSplit'
import type { SplitPayload } from '../components/SplitDialog'

const PAYLOAD: SplitPayload = {
    file_id: 'abc-123',
    direction: 'vertical',
    ratio: 0.5,
    exclude_pages: [],
    rtl: false,
    deskew: true,
}

describe('requestSplit', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn())
    })

    afterEach(() => {
        vi.unstubAllGlobals()
    })

    it('POSTs the payload as JSON to the /split endpoint', async () => {
        const fakeBlob = new Blob(['fake-pdf'], { type: 'application/pdf' })
        ;(fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
            ok: true,
            blob: () => Promise.resolve(fakeBlob),
        })

        const result = await requestSplit('http://localhost:8000', PAYLOAD)

        expect(fetch).toHaveBeenCalledOnce()
        const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
        expect(url).toBe('http://localhost:8000/split')
        expect(init.method).toBe('POST')
        expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' })
        expect(JSON.parse(init.body)).toEqual(PAYLOAD)
        expect(result).toBe(fakeBlob)
    })

    it('throws an error when the response is not ok', async () => {
        ;(fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
            ok: false,
            status: 500,
            text: () => Promise.resolve('boom'),
        })

        await expect(requestSplit('http://localhost:8000', PAYLOAD)).rejects.toThrow(/500/)
    })

    it('propagates network errors', async () => {
        ;(fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'))
        await expect(requestSplit('http://localhost:8000', PAYLOAD)).rejects.toThrow(/network down/)
    })
})
