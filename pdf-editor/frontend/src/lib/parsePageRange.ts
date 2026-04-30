/**
 * Parse a 1-indexed user-facing page-range string into 0-indexed page numbers.
 *
 * Examples:
 *   ""        → []
 *   "1"       → [0]
 *   "1,3,5-7" → [0, 2, 4, 5, 6]
 *
 * Throws on zero/negative pages, malformed ranges (start > end), or non-numeric tokens.
 */
export function parsePageRange(input: string): number[] {
    const trimmed = input.trim()
    if (trimmed === '') return []

    const collected = new Set<number>()

    for (const rawSegment of trimmed.split(',')) {
        const segment = rawSegment.trim()
        if (segment === '') continue

        if (segment.includes('-')) {
            const [rawStart, rawEnd] = segment.split('-').map((s) => s.trim())
            const start = parsePositiveInt(rawStart, segment)
            const end = parsePositiveInt(rawEnd, segment)
            if (start > end) {
                throw new Error(`Invalid range "${segment}": start ${start} > end ${end}`)
            }
            for (let i = start; i <= end; i++) collected.add(i - 1)
        } else {
            const page = parsePositiveInt(segment, segment)
            collected.add(page - 1)
        }
    }

    return Array.from(collected).sort((a, b) => a - b)
}

function parsePositiveInt(token: string, context: string): number {
    if (!/^\d+$/.test(token)) {
        throw new Error(`Invalid page token "${token}" in "${context}"`)
    }
    const value = Number(token)
    if (!Number.isInteger(value) || value < 1) {
        throw new Error(`Page numbers must be >= 1 (got ${value} in "${context}")`)
    }
    return value
}
