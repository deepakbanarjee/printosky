import type { SplitPayload } from '../components/SplitDialog'

export async function requestSplit(baseUrl: string, payload: SplitPayload): Promise<Blob> {
    const response = await fetch(`${baseUrl}/split`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })

    if (!response.ok) {
        const detail = await response.text().catch(() => '')
        throw new Error(`Split request failed (${response.status})${detail ? `: ${detail}` : ''}`)
    }

    return response.blob()
}
