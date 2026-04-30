const raw = (import.meta.env.VITE_API_BASE ?? '').trim()

export const API_BASE: string = (raw || 'http://127.0.0.1:8000').replace(/\/+$/, '')
