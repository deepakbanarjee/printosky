import { describe, it, expect } from 'vitest'
import { parsePageRange } from './parsePageRange'

describe('parsePageRange', () => {
    it('returns empty array for empty string', () => {
        expect(parsePageRange('')).toEqual([])
    })

    it('returns empty array for whitespace-only string', () => {
        expect(parsePageRange('   ')).toEqual([])
    })

    it('parses a single 1-indexed page into a 0-indexed number', () => {
        // user types "1" meaning the first page → 0-indexed 0
        expect(parsePageRange('1')).toEqual([0])
    })

    it('parses a comma-separated list of single pages', () => {
        // "1,3,5" (1-indexed) → [0,2,4] (0-indexed)
        expect(parsePageRange('1,3,5')).toEqual([0, 2, 4])
    })

    it('parses an inclusive range "5-7" into 0-indexed [4,5,6]', () => {
        expect(parsePageRange('5-7')).toEqual([4, 5, 6])
    })

    it('parses a mixed list "1,3,5-7"', () => {
        expect(parsePageRange('1,3,5-7')).toEqual([0, 2, 4, 5, 6])
    })

    it('tolerates surrounding whitespace and empty segments', () => {
        expect(parsePageRange(' 1 , , 3 - 4 ')).toEqual([0, 2, 3])
    })

    it('deduplicates and sorts overlapping ranges', () => {
        expect(parsePageRange('5-7,3,6')).toEqual([2, 4, 5, 6])
    })

    it('throws when a page number is zero or negative', () => {
        expect(() => parsePageRange('0')).toThrow()
        expect(() => parsePageRange('-1')).toThrow()
    })

    it('throws on a malformed range where start > end', () => {
        expect(() => parsePageRange('5-3')).toThrow()
    })

    it('throws on non-numeric input', () => {
        expect(() => parsePageRange('abc')).toThrow()
        expect(() => parsePageRange('1,abc,3')).toThrow()
    })
})
