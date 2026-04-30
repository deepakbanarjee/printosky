export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024 // 20 MB
export const MAX_PAGE_COUNT = 50

export type ValidationResult = { ok: true } | { ok: false; error: string }

export function validateUpload(file: File): ValidationResult {
  const looksLikePdfByExt = /\.pdf$/i.test(file.name)
  const looksLikePdfByMime = file.type === 'application/pdf'
  if (!looksLikePdfByMime && !looksLikePdfByExt) {
    return { ok: false, error: 'File must be a PDF (.pdf).' }
  }
  if (file.size === 0) {
    return { ok: false, error: 'File is empty.' }
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1)
    return { ok: false, error: `File is ${mb} MB — too large. Maximum 20 MB.` }
  }
  return { ok: true }
}

export function validatePageCount(count: number): ValidationResult {
  if (!Number.isFinite(count) || count <= 0) {
    return { ok: false, error: 'PDF appears to have no readable pages.' }
  }
  if (count > MAX_PAGE_COUNT) {
    return {
      ok: false,
      error: `PDF has ${count} pages — over the ${MAX_PAGE_COUNT}-page limit.`,
    }
  }
  return { ok: true }
}
