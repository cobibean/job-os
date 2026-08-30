import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { canonicalWorkspace } from './workspaceLayout'
import { WorkbenchLayout } from './WorkbenchLayout'

afterEach(() => {
  vi.restoreAllMocks()
  cleanup()
})

function mockPanelBounds() {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const panel = this.getAttribute('data-testid')
    const x = panel === 'panel-jobs' ? 0 : panel === 'panel-center' ? 100 : panel === 'panel-agent' ? 200 : 0
    return DOMRect.fromRect({ x, y: 100, width: 100, height: 400 })
  })
}

function layout(onMove = vi.fn(), onReorderInteractionChange = vi.fn()) {
  return (
    <WorkbenchLayout
      agent={<div />}
      center={<div />}
      jobs={<div />}
      onCollapse={vi.fn()}
      onMove={onMove}
      onReorderInteractionChange={onReorderInteractionChange}
      onResize={vi.fn()}
      workspace={canonicalWorkspace()}
    />
  )
}

test('the visible arrange grip directly reorders a panel', async () => {
  mockPanelBounds()
  const onMove = vi.fn()
  const view = render(layout(onMove))
  const visibleGrip = view.container.querySelector('summary[aria-label="Arrange Agent chat"]')

  expect(visibleGrip).not.toBeNull()
  expect(visibleGrip?.tagName).toBe('SUMMARY')
  fireEvent.pointerDown(visibleGrip as HTMLElement, {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 150, pointerId: 1 })
  fireEvent.pointerUp(window, { clientX: 150, clientY: 150, pointerId: 1 })

  expect(onMove).toHaveBeenCalledWith('agent', 1)
})

test('clicking the visible arrange grip still opens its move controls', () => {
  const view = render(layout())
  const visibleGrip = view.container.querySelector('summary[aria-label="Arrange Agent chat"]')
  const menu = visibleGrip?.closest('details')

  expect(menu?.open).toBe(false)
  fireEvent.pointerDown(visibleGrip as HTMLElement, {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  fireEvent.pointerUp(window, { clientX: 250, clientY: 150, pointerId: 1 })
  fireEvent.click(visibleGrip as HTMLElement)

  expect(menu?.open).toBe(true)
})

test('a canceled drag does not swallow the next arrange click', async () => {
  mockPanelBounds()
  const view = render(layout())
  const visibleGrip = view.container.querySelector('summary[aria-label="Arrange Agent chat"]')
  const menu = visibleGrip?.closest('details')

  fireEvent.pointerDown(visibleGrip as HTMLElement, {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 150, pointerId: 1 })
  fireEvent.pointerCancel(window, { clientX: 150, clientY: 150, pointerId: 1 })
  fireEvent.click(visibleGrip as HTMLElement)

  expect(menu?.open).toBe(true)
})

test('unmounting during a reorder releases the native-surface interaction state', () => {
  const onReorderInteractionChange = vi.fn()
  const view = render(layout(vi.fn(), onReorderInteractionChange))

  fireEvent.pointerDown(screen.getByTitle('Drag to reorder Agent chat; click for move controls'), {
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  expect(onReorderInteractionChange).toHaveBeenLastCalledWith(true)

  view.unmount()
  expect(onReorderInteractionChange).toHaveBeenLastCalledWith(false)
})

test('releasing outside the panels cancels the reorder', async () => {
  mockPanelBounds()
  const onMove = vi.fn()
  render(layout(onMove))

  fireEvent.pointerDown(screen.getByTitle('Drag to reorder Agent chat; click for move controls'), {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 50, pointerId: 1 })
  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(false)
  fireEvent.pointerUp(window, { clientX: 150, clientY: 50, pointerId: 1 })

  expect(onMove).not.toHaveBeenCalled()
})

test('changing the interaction callback mid-gesture clears the insertion target', async () => {
  mockPanelBounds()
  const firstInteraction = vi.fn()
  const view = render(layout(vi.fn(), firstInteraction))

  fireEvent.pointerDown(screen.getByTitle('Drag to reorder Agent chat; click for move controls'), {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 150, pointerId: 1 })
  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(true)

  view.rerender(layout(vi.fn(), vi.fn()))

  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(false)
  expect(firstInteraction).toHaveBeenLastCalledWith(false)
})

test('Escape cancels an active pointer reorder', async () => {
  mockPanelBounds()
  const onMove = vi.fn()
  const onReorderInteractionChange = vi.fn()
  render(layout(onMove, onReorderInteractionChange))

  fireEvent.pointerDown(screen.getByTitle('Drag to reorder Agent chat; click for move controls'), {
    button: 0,
    clientX: 250,
    clientY: 150,
    pointerId: 1
  })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 150, pointerId: 1 })
  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(true)

  fireEvent.keyDown(window, { key: 'Escape' })
  fireEvent.pointerUp(window, { clientX: 150, clientY: 150, pointerId: 1 })

  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(false)
  expect(onMove).not.toHaveBeenCalled()
  expect(onReorderInteractionChange).toHaveBeenLastCalledWith(false)
})

test('a stale approval cannot affect a newer gesture that reuses the pointer id', async () => {
  mockPanelBounds()
  const onMove = vi.fn()
  let resolveFirst!: (approved: boolean) => void
  let firstStart = true
  const onReorderInteractionChange = vi.fn((active: boolean) => {
    if (!active) return true
    if (!firstStart) return true
    firstStart = false
    return new Promise<boolean>(resolve => { resolveFirst = resolve })
  })
  render(layout(onMove, onReorderInteractionChange))
  const control = screen.getByTitle('Drag to reorder Agent chat; click for move controls')

  fireEvent.pointerDown(control, { button: 0, clientX: 250, clientY: 150, pointerId: 1 })
  fireEvent.pointerUp(window, { clientX: 250, clientY: 150, pointerId: 1 })
  fireEvent.pointerDown(control, { button: 0, clientX: 250, clientY: 150, pointerId: 1 })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 150, clientY: 150, pointerId: 1 })
  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(true)

  await act(async () => resolveFirst(false))
  expect(screen.getByTestId('panel-center').classList.contains('insertion-target')).toBe(true)
  fireEvent.pointerUp(window, { clientX: 150, clientY: 150, pointerId: 1 })

  expect(onMove).toHaveBeenCalledWith('agent', 1)
})
