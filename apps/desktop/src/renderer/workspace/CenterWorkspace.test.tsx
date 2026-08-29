import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'

import { CenterWorkspace } from './CenterWorkspace'

afterEach(cleanup)

test('mounts only the active center surface', () => {
  const view = render(
    <CenterWorkspace
      activeSurface="browser"
      browser={<div>Browser surface</div>}
      document={<div>Document surface</div>}
    />
  )

  expect(screen.getByText('Browser surface')).not.toBeNull()
  expect(screen.queryByText('Document surface')).toBeNull()

  view.rerender(
    <CenterWorkspace
      activeSurface="document"
      browser={<div>Browser surface</div>}
      document={<div>Document surface</div>}
    />
  )

  expect(screen.queryByText('Browser surface')).toBeNull()
  expect(screen.getByText('Document surface')).not.toBeNull()
})
