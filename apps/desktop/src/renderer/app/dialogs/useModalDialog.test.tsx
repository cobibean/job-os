import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test } from 'vitest'
import { Dialog } from '../../career-profile/product/dialogs/Dialog'

afterEach(cleanup)

function NestedDialogs() {
  const [outer, setOuter] = useState(false)
  const [inner, setInner] = useState(false)
  return <><button onClick={() => setOuter(true)}>Open outer</button>{outer ? <Dialog label="Outer" onClose={() => setOuter(false)}><button onClick={() => setInner(true)}>Open inner</button><button>Last outer</button>{inner ? <Dialog label="Inner" onClose={() => setInner(false)}><button>First inner</button><button>Last inner</button></Dialog> : null}</Dialog> : null}</>
}

test('nested dialogs dismiss only the top layer and restore its opener before the outer opener', async () => {
  render(<NestedDialogs />)
  const opener = screen.getByRole('button', { name: 'Open outer' })
  opener.focus()
  fireEvent.click(opener)
  const outer = screen.getByRole('dialog', { name: 'Outer' })
  const innerOpener = within(outer).getByRole('button', { name: 'Open inner' })
  innerOpener.focus()
  fireEvent.click(innerOpener)
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'First inner' }))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: 'Inner' })).toBeNull()
  expect(screen.getByRole('dialog', { name: 'Outer' })).toBe(outer)
  await waitFor(() => expect(document.activeElement).toBe(innerOpener))
  fireEvent.keyDown(document, { key: 'Escape' })
  await waitFor(() => expect(document.activeElement).toBe(opener))
})

test('background focus cannot escape a modal and preexisting inert state survives dismissal', () => {
  const alreadyInert = document.createElement('div')
  alreadyInert.inert = true
  document.body.append(alreadyInert)
  try {
    render(<NestedDialogs />)
    const opener = screen.getByRole('button', { name: 'Open outer' })
    fireEvent.click(opener)
    expect(document.querySelector<HTMLElement>('.career-product-backdrop')?.inert).not.toBe(true)
    opener.focus()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open inner' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(alreadyInert.inert).toBe(true)
  } finally { alreadyInert.remove() }
})
