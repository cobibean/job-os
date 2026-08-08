interface VisibleWindow {
  isDestroyed: () => boolean
  show: () => void
}

export async function activateVisibleWindow<T extends VisibleWindow>(
  current: T | null,
  create: () => Promise<T>
): Promise<T> {
  if (!current || current.isDestroyed()) return create()
  current.show()
  return current
}
