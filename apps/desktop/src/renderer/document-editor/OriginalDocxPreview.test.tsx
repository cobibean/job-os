// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { renderDocx } = vi.hoisted(() => ({
  renderDocx: vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
    const link = document.createElement('a')
    link.href = 'https://example.com/external'
    link.textContent = 'External link'
    container.append(link)
  })
}))

vi.mock('docx-preview', () => ({ renderAsync: renderDocx }))

import { OriginalDocxPreview } from './OriginalDocxPreview'

beforeEach(() => {
  renderDocx.mockClear()
  window.jobos = {
    documents: {
      loadOriginalDocx: vi.fn(async () => ({
        artifactId: 'art_ABCDEFGHIJKLMNOPQRSTUVWX',
        filename: 'original.docx',
        sha256: 'a'.repeat(64),
        bytes: new ArrayBuffer(4)
      }))
    }
  } as unknown as typeof window.jobos
})

afterEach(cleanup)

describe('OriginalDocxPreview', () => {
  it('renders only checksum-verified bridge bytes and disables rendered navigation', async () => {
    render(<OriginalDocxPreview artifactId="art_ABCDEFGHIJKLMNOPQRSTUVWX" sourceFilename="original.docx" />)

    await screen.findByText(/SHA-256 verified/)
    expect(renderDocx).toHaveBeenCalledOnce()
    const link = screen.getByText('External link')
    expect(link.getAttribute('href')).toBeNull()
    expect(link.getAttribute('aria-disabled')).toBe('true')
    const event = new MouseEvent('click', { bubbles: true, cancelable: true })
    link.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it('fails closed when trusted bytes cannot be loaded', async () => {
    vi.mocked(window.jobos.documents.loadOriginalDocx).mockRejectedValueOnce(new Error('checksum mismatch'))
    render(<OriginalDocxPreview artifactId="art_ABCDEFGHIJKLMNOPQRSTUVWX" sourceFilename={null} />)
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('could not be verified'))
    expect(renderDocx).not.toHaveBeenCalled()
  })
})
