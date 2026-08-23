interface VisibleWindow {
  isDestroyed: () => boolean
  show: () => void
}

export type RendererSafetyReason = 'window-close' | 'profile-switch'

interface PendingSafetyRequest {
  resolve: (safe: boolean) => void
  timer: ReturnType<typeof setTimeout>
}

export class RendererSafetyCoordinator {
  readonly #pending = new Map<string, PendingSafetyRequest>()

  constructor(
    private readonly send: (requestId: string, reason: RendererSafetyReason) => void,
    private readonly timeoutMs = 15_000,
    private readonly identifier = () => `safety_${crypto.randomUUID()}`
  ) {}

  request(reason: RendererSafetyReason): Promise<boolean> {
    if (this.#pending.size > 0) return Promise.resolve(false)
    const requestId = this.identifier()
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this.#pending.delete(requestId)
        resolve(false)
      }, this.timeoutMs)
      this.#pending.set(requestId, { resolve, timer })
      this.send(requestId, reason)
    })
  }

  resolve(requestId: unknown, safe: unknown): boolean {
    if (typeof requestId !== 'string') return false
    const pending = this.#pending.get(requestId)
    if (!pending) return false
    clearTimeout(pending.timer)
    this.#pending.delete(requestId)
    pending.resolve(safe === true)
    return true
  }

  dispose(): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer)
      pending.resolve(false)
    }
    this.#pending.clear()
  }
}

export async function activateVisibleWindow<T extends VisibleWindow>(
  current: T | null,
  create: () => Promise<T>
): Promise<T> {
  if (!current || current.isDestroyed()) return create()
  current.show()
  return current
}
