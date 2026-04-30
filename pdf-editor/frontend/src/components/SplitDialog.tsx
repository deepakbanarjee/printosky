import { useState } from 'react'
import { parsePageRange } from '../lib/parsePageRange'

export interface SplitPayload {
    file_id: string
    direction: 'vertical' | 'horizontal'
    ratio: number
    exclude_pages: number[]
    rtl: boolean
    deskew: boolean
}

export interface SplitDialogProps {
    open: boolean
    fileId: string
    onClose: () => void
    onSubmit: (payload: SplitPayload) => void
}

function SplitDialog({ open, fileId, onClose, onSubmit }: SplitDialogProps) {
    const [direction, setDirection] = useState<'vertical' | 'horizontal'>('vertical')
    const [ratio, setRatio] = useState(0.5)
    const [excludeText, setExcludeText] = useState('')
    const [rtl, setRtl] = useState(false)
    const [deskew, setDeskew] = useState(true)
    const [error, setError] = useState<string | null>(null)

    if (!open) return null

    const handleSubmit = () => {
        let exclude_pages: number[]
        try {
            exclude_pages = parsePageRange(excludeText)
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Invalid exclude-pages input')
            return
        }
        setError(null)
        onSubmit({
            file_id: fileId,
            direction,
            ratio,
            exclude_pages,
            rtl,
            deskew,
        })
    }

    return (
        <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="split-dialog-title"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        >
            <div className="bg-white rounded-2xl shadow-2xl w-[480px] max-w-[90vw] p-6 space-y-5">
                <h2 id="split-dialog-title" className="text-xl font-bold text-slate-900">
                    Split PDF Down the Middle
                </h2>

                <fieldset className="space-y-2">
                    <legend className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                        Direction
                    </legend>
                    <div className="flex gap-4">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="radio"
                                name="direction"
                                value="vertical"
                                checked={direction === 'vertical'}
                                onChange={() => setDirection('vertical')}
                            />
                            <span>Vertical (left / right)</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="radio"
                                name="direction"
                                value="horizontal"
                                checked={direction === 'horizontal'}
                                onChange={() => setDirection('horizontal')}
                            />
                            <span>Horizontal (top / bottom)</span>
                        </label>
                    </div>
                </fieldset>

                <div className="space-y-1">
                    <label htmlFor="split-ratio" className="text-xs font-bold text-slate-500 uppercase tracking-wider block">
                        Split position
                    </label>
                    <input
                        id="split-ratio"
                        type="range"
                        min={0.05}
                        max={0.95}
                        step={0.01}
                        value={ratio}
                        onChange={(e) => setRatio(parseFloat(e.target.value))}
                        className="w-full"
                    />
                    <div className="text-xs text-slate-500">{Math.round(ratio * 100)}%</div>
                </div>

                <div className="space-y-1">
                    <label htmlFor="exclude-pages" className="text-xs font-bold text-slate-500 uppercase tracking-wider block">
                        Exclude pages (e.g. 1,3,5-7)
                    </label>
                    <input
                        id="exclude-pages"
                        type="text"
                        value={excludeText}
                        onChange={(e) => setExcludeText(e.target.value)}
                        placeholder="leave blank to split all pages"
                        className="w-full border-slate-300 rounded-lg p-2 text-sm"
                    />
                </div>

                <div className="space-y-2">
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={deskew}
                            onChange={(e) => setDeskew(e.target.checked)}
                        />
                        <span className="text-sm">Auto-deskew scanned pages before splitting</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={rtl}
                            onChange={(e) => setRtl(e.target.checked)}
                        />
                        <span className="text-sm">Right-to-left order (Arabic / Hebrew / manga)</span>
                    </label>
                </div>

                {error && (
                    <div role="alert" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-2">
                        {error}
                    </div>
                )}

                <div className="flex justify-end gap-2 pt-2">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 rounded-lg text-slate-600 hover:bg-slate-100 text-sm font-medium"
                    >
                        Cancel
                    </button>
                    <button
                        type="button"
                        onClick={handleSubmit}
                        className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 text-sm font-medium"
                    >
                        Split & Download
                    </button>
                </div>
            </div>
        </div>
    )
}

export default SplitDialog
