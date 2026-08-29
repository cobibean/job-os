// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { renderDocx } = vi.hoisted(() => ({ renderDocx: vi.fn() }))

vi.mock('docx-preview', () => ({ renderAsync: renderDocx }))

import { DocxBytesPreview } from './DocxBytesPreview'

afterEach(() => {
  cleanup()
  renderDocx.mockReset()
})

describe('DocxBytesPreview', () => {
  it('commits only the newest asynchronous DOCX render', async () => {
    let finishOldRender!: () => void
    renderDocx
      .mockImplementationOnce(async (_bytes: ArrayBuffer, container: HTMLElement) => {
        await new Promise<void>(resolve => { finishOldRender = resolve })
        container.textContent = 'stale editable bytes'
      })
      .mockImplementationOnce(async (_bytes: ArrayBuffer, container: HTMLElement) => {
        container.textContent = 'current editable bytes'
      })
    const view = render(
      <DocxBytesPreview bytes={Uint8Array.of(1).buffer} filename="old.docx" label="Current editable DOCX" sha256={'a'.repeat(64)} />
    )
    await waitFor(() => expect(renderDocx).toHaveBeenCalledTimes(1))

    view.rerender(
      <DocxBytesPreview bytes={Uint8Array.of(2).buffer} filename="new.docx" label="Current editable DOCX" sha256={'b'.repeat(64)} />
    )
    expect(await screen.findByText('current editable bytes')).not.toBeNull()

    await act(async () => { finishOldRender(); await Promise.resolve() })
    expect(screen.queryByText('stale editable bytes')).toBeNull()
    expect(screen.getByText('current editable bytes')).not.toBeNull()
  })
})