import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SplitDialog, { SplitPayload } from './SplitDialog'

function setup(overrides: Partial<React.ComponentProps<typeof SplitDialog>> = {}) {
    const onClose = vi.fn()
    const onSubmit = vi.fn()
    const props = {
        open: true,
        fileId: 'test-file-id',
        onClose,
        onSubmit,
        ...overrides,
    }
    const utils = render(<SplitDialog {...props} />)
    return { ...utils, onClose, onSubmit, user: userEvent.setup() }
}

describe('SplitDialog', () => {
    it('does not render content when open is false', () => {
        setup({ open: false })
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('renders a dialog when open is true', () => {
        setup()
        expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('defaults the direction radio to vertical', () => {
        setup()
        const vertical = screen.getByLabelText(/vertical/i) as HTMLInputElement
        expect(vertical.checked).toBe(true)
    })

    it('defaults the deskew checkbox to checked', () => {
        setup()
        const deskew = screen.getByLabelText(/auto-deskew/i) as HTMLInputElement
        expect(deskew.checked).toBe(true)
    })

    it('defaults the rtl checkbox to unchecked', () => {
        setup()
        const rtl = screen.getByLabelText(/right-to-left/i) as HTMLInputElement
        expect(rtl.checked).toBe(false)
    })

    it('defaults the ratio slider to 0.5', () => {
        setup()
        const ratio = screen.getByLabelText(/split position/i) as HTMLInputElement
        expect(parseFloat(ratio.value)).toBeCloseTo(0.5)
    })

    it('fires onClose when Cancel is clicked', async () => {
        const { user, onClose } = setup()
        await user.click(screen.getByRole('button', { name: /cancel/i }))
        expect(onClose).toHaveBeenCalledOnce()
    })

    it('submits default values when Split & Download is clicked', async () => {
        const { user, onSubmit } = setup()
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        expect(onSubmit).toHaveBeenCalledOnce()
        const payload = onSubmit.mock.calls[0][0] as SplitPayload
        expect(payload).toEqual({
            file_id: 'test-file-id',
            direction: 'vertical',
            ratio: 0.5,
            exclude_pages: [],
            rtl: false,
            deskew: true,
        })
    })

    it('submits horizontal direction when user selects it', async () => {
        const { user, onSubmit } = setup()
        await user.click(screen.getByLabelText(/horizontal/i))
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        const payload = onSubmit.mock.calls[0][0] as SplitPayload
        expect(payload.direction).toBe('horizontal')
    })

    it('submits rtl=true when user toggles the checkbox', async () => {
        const { user, onSubmit } = setup()
        await user.click(screen.getByLabelText(/right-to-left/i))
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        expect((onSubmit.mock.calls[0][0] as SplitPayload).rtl).toBe(true)
    })

    it('submits deskew=false when user unchecks it', async () => {
        const { user, onSubmit } = setup()
        await user.click(screen.getByLabelText(/auto-deskew/i))
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        expect((onSubmit.mock.calls[0][0] as SplitPayload).deskew).toBe(false)
    })

    it('submits parsed exclude_pages when user enters a range', async () => {
        const { user, onSubmit } = setup()
        await user.type(screen.getByLabelText(/exclude pages/i), '1,3,5-7')
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        expect((onSubmit.mock.calls[0][0] as SplitPayload).exclude_pages).toEqual([0, 2, 4, 5, 6])
    })

    it('shows an error and does not submit when exclude_pages is malformed', async () => {
        const { user, onSubmit } = setup()
        await user.type(screen.getByLabelText(/exclude pages/i), 'abc')
        await user.click(screen.getByRole('button', { name: /split.*download/i }))
        expect(onSubmit).not.toHaveBeenCalled()
        expect(screen.getByRole('alert')).toBeInTheDocument()
    })
})
