import { describe, it, expect } from 'vitest'
import { validateUpload, MAX_UPLOAD_BYTES, MAX_PAGE_COUNT, validatePageCount } from './validateUpload'

function makeFile(opts: { name?: string; type?: string; size?: number } = {}): File {
  const { name = 'doc.pdf', type = 'application/pdf', size = 1024 } = opts
  const blob = new Blob([new Uint8Array(size)], { type })
  return new File([blob], name, { type })
}

describe('validateUpload', () => {
  it('accepts a normal PDF', () => {
    const r = validateUpload(makeFile())
    expect(r.ok).toBe(true)
  })

  it('rejects a non-PDF MIME type', () => {
    const r = validateUpload(makeFile({ type: 'image/png', name: 'x.png' }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/pdf/i)
  })

  it('rejects a file above the size limit', () => {
    const r = validateUpload(makeFile({ size: MAX_UPLOAD_BYTES + 1 }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/20\s*MB|too large/i)
  })

  it('accepts a file exactly at the size limit', () => {
    const r = validateUpload(makeFile({ size: MAX_UPLOAD_BYTES }))
    expect(r.ok).toBe(true)
  })

  it('rejects an empty file', () => {
    const r = validateUpload(makeFile({ size: 0 }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/empty/i)
  })

  it('treats .pdf extension as acceptable even when browser reports no mime', () => {
    const r = validateUpload(makeFile({ type: '', name: 'scan.pdf' }))
    expect(r.ok).toBe(true)
  })
})

describe('validatePageCount', () => {
  it('accepts within limit', () => {
    const r = validatePageCount(MAX_PAGE_COUNT)
    expect(r.ok).toBe(true)
  })
  it('rejects above limit', () => {
    const r = validatePageCount(MAX_PAGE_COUNT + 1)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(new RegExp(String(MAX_PAGE_COUNT)))
  })
  it('rejects zero pages', () => {
    const r = validatePageCount(0)
    expect(r.ok).toBe(false)
  })
})
