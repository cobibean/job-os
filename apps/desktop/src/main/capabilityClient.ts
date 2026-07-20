import type { BrowserSemanticSnapshot, BrowserState } from '../shared/contracts.js'

const COMMANDS = new Set([
  'tabs.inspect', 'tab.create', 'tab.select', 'tab.close', 'tabs.reorder',
  'tab.navigate', 'tab.back', 'tab.forward', 'tab.reload', 'tab.stop',
  'page.snapshot', 'element.click', 'element.type', 'page.scroll'
])
const TAB_ID = /^[A-Za-z0-9_-]{1,128}$/
const TARGET_ID = /^t_[A-Za-z0-9_-]{1,64}$/
const COMMAND_ID = /^cmd_[A-Za-z0-9_-]{8,80}$/

function ordinaryUrl(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 8192) return false
  try {
    const protocol = new URL(value).protocol
    return protocol === 'http:' || protocol === 'https:'
  } catch { return false }
}

interface BrowserCapabilities {
  inspect: () => BrowserState
  create?: (url?: string, associatedJobId?: string | null) => Promise<BrowserState>
  select?: (tabId: string) => BrowserState
  close?: (tabId: string) => Promise<BrowserState>
  reorder?: (tabIds: string[]) => BrowserState
  navigate?: (tabId: string, url: string) => Promise<BrowserState>
  back?: (tabId: string) => BrowserState
  forward?: (tabId: string) => BrowserState
  reload?: (tabId: string) => BrowserState
  stop?: (tabId: string) => BrowserState
  snapshot?: (tabId: string) => Promise<BrowserSemanticSnapshot>
  click?: (tabId: string, targetId: string) => Promise<BrowserState>
  type?: (tabId: string, targetId: string, text: string, clear?: boolean) => Promise<BrowserState>
  scroll?: (tabId: string, direction: 'up' | 'down', amount?: number) => Promise<BrowserState>
}

interface CapabilityCommand {
  type: string
  command_id: string
  idempotency_key: string
  origin: string
  deadline_at: string
  command: string
  arguments: Record<string, unknown>
}

interface CapabilityResult {
  type: 'result'
  command_id: string
  state: 'completed' | 'failed'
  outcome: string
  data?: Record<string, unknown>
  error?: { code: 'tab_not_found' | 'timeout' | 'validation' | 'execution'; message: string }
}

function safeState(state: BrowserState): Record<string, unknown> {
  return {
    active_tab_id: state.activeTabId,
    tabs: state.tabs.slice(0, 50).map(tab => ({
      tab_id: tab.tabId,
      url: tab.url,
      title: tab.title,
      associated_job_id: tab.associatedJobId,
      loading: tab.loading,
      can_go_back: tab.canGoBack,
      can_go_forward: tab.canGoForward,
      crashed: tab.crashed
    }))
  }
}

function failed(
  commandId: string,
  code: CapabilityResult['error'] extends infer Error | undefined
    ? Error extends { code: infer Code } ? Code : never : never,
  message: string
): CapabilityResult {
  return { type: 'result', command_id: commandId, state: 'failed', outcome: code,
    error: { code, message } }
}

function requiredMethod<T extends keyof BrowserCapabilities>(
  manager: BrowserCapabilities, name: T
): NonNullable<BrowserCapabilities[T]> {
  const method = manager[name]
  if (typeof method !== 'function') throw new Error('Browser capability unavailable')
  return method.bind(manager) as NonNullable<BrowserCapabilities[T]>
}

export async function dispatchCapabilityCommand(
  manager: BrowserCapabilities,
  payload: CapabilityCommand
): Promise<CapabilityResult> {
  const commandId = typeof payload.command_id === 'string' ? payload.command_id : 'invalid'
  try {
    if (payload.type !== 'command' || !COMMAND_ID.test(commandId)
      || typeof payload.idempotency_key !== 'string'
      || payload.idempotency_key.length < 1 || payload.idempotency_key.length > 128
      || (payload.origin !== 'user' && payload.origin !== 'mcp')
      || !COMMANDS.has(payload.command) || !payload.arguments
      || typeof payload.arguments !== 'object') {
      return failed(commandId, 'validation', 'Unsupported or invalid browser command.')
    }
    const deadline = Date.parse(payload.deadline_at)
    if (!Number.isFinite(deadline) || deadline <= Date.now()
      || deadline > Date.now() + 15_000) {
      return failed(commandId, 'timeout', 'Browser command deadline elapsed before execution.')
    }
    const args = payload.arguments
    const tabId = args.tab_id
    if (!['tabs.inspect', 'tab.create', 'tabs.reorder'].includes(payload.command)
      && (typeof tabId !== 'string' || !TAB_ID.test(tabId))) {
      return failed(commandId, 'validation', 'Invalid browser tab identifier.')
    }
    let data: Record<string, unknown>
    switch (payload.command) {
      case 'tabs.inspect': data = safeState(manager.inspect()); break
      case 'tab.create': {
        if ((args.url !== undefined && !ordinaryUrl(args.url))
          || (args.associated_job_id !== undefined && args.associated_job_id !== null
            && (typeof args.associated_job_id !== 'string'
              || args.associated_job_id.length > 512))) throw new TypeError()
        data = safeState(await requiredMethod(manager, 'create')(
          args.url as string | undefined,
          typeof args.associated_job_id === 'string' ? args.associated_job_id : null))
        break
      }
      case 'tab.select': data = safeState(requiredMethod(manager, 'select')(tabId as string)); break
      case 'tab.close': data = safeState(await requiredMethod(manager, 'close')(tabId as string)); break
      case 'tabs.reorder': {
        if (!Array.isArray(args.tab_ids) || args.tab_ids.length < 1
          || args.tab_ids.length > 50 || new Set(args.tab_ids).size !== args.tab_ids.length
          || args.tab_ids.some(id => typeof id !== 'string' || !TAB_ID.test(id))) throw new TypeError()
        data = safeState(requiredMethod(manager, 'reorder')(args.tab_ids as string[])); break
      }
      case 'tab.navigate': {
        if (!ordinaryUrl(args.url)) throw new TypeError()
        data = safeState(await requiredMethod(manager, 'navigate')(tabId as string, args.url)); break
      }
      case 'tab.back': data = safeState(requiredMethod(manager, 'back')(tabId as string)); break
      case 'tab.forward': data = safeState(requiredMethod(manager, 'forward')(tabId as string)); break
      case 'tab.reload': data = safeState(requiredMethod(manager, 'reload')(tabId as string)); break
      case 'tab.stop': data = safeState(requiredMethod(manager, 'stop')(tabId as string)); break
      case 'page.snapshot': {
        const snapshot = await requiredMethod(manager, 'snapshot')(tabId as string)
        data = { tab_id: snapshot.tabId, url: snapshot.url, title: snapshot.title,
          text: snapshot.text.slice(0, 5000), elements: snapshot.elements.slice(0, 100).map(element => ({
            target_id: element.targetId, role: element.role, name: element.name,
            disabled: element.disabled
          })) }
        break
      }
      case 'element.click': {
        if (typeof args.target_id !== 'string' || !TARGET_ID.test(args.target_id)) throw new TypeError()
        data = safeState(await requiredMethod(manager, 'click')(tabId as string, args.target_id)); break
      }
      case 'element.type': {
        if (typeof args.target_id !== 'string' || !TARGET_ID.test(args.target_id)
          || typeof args.text !== 'string' || args.text.length > 4000
          || (args.clear !== undefined && typeof args.clear !== 'boolean')) throw new TypeError()
        data = safeState(await requiredMethod(manager, 'type')(
          tabId as string, args.target_id, args.text, args.clear !== false)); break
      }
      case 'page.scroll': {
        const amount = args.amount === undefined ? 600 : Number(args.amount)
        if ((args.direction !== 'up' && args.direction !== 'down')
          || !Number.isInteger(amount) || amount < 1 || amount > 2000) throw new TypeError()
        data = safeState(await requiredMethod(manager, 'scroll')(
          tabId as string, args.direction, amount)); break
      }
      default: return failed(commandId, 'validation', 'Unsupported browser command.')
    }
    return { type: 'result', command_id: commandId, state: 'completed',
      outcome: payload.command, data }
  } catch (error) {
    if (error instanceof TypeError) {
      return failed(commandId, 'validation', 'Invalid browser command arguments.')
    }
    if (error instanceof Error && error.message.toLowerCase().includes('tab not found')) {
      return failed(commandId, 'tab_not_found', 'The browser tab no longer exists; inspect tabs and retry.')
    }
    return failed(commandId, 'execution', 'The desktop could not complete the browser command.')
  }
}

interface SocketLike {
  readyState: number
  addEventListener: (name: string, listener: (event: { data?: string }) => void) => void
  send: (value: string) => void
  close: () => void
}

interface CapabilityConfig { baseUrl: string; deviceToken: string; deviceId: string }
interface CapabilityDependencies {
  socketFactory?: (url: string) => SocketLike
  setTimer?: (callback: () => void, delay: number) => NodeJS.Timeout
  clearTimer?: (timer: NodeJS.Timeout) => void
  browserReady?: Promise<void>
}

export function startDesktopCapabilityClient(
  manager: BrowserCapabilities,
  config: CapabilityConfig,
  dependencies: CapabilityDependencies = {}
): () => void {
  const socketFactory = dependencies.socketFactory
    ?? ((url: string) => new WebSocket(url) as unknown as SocketLike)
  const setTimer = dependencies.setTimer
    ?? ((callback, delay) => setTimeout(callback, delay) as unknown as NodeJS.Timeout)
  const clearTimer = dependencies.clearTimer ?? (timer => clearTimeout(timer))
  let socket: SocketLike | null = null
  let stopped = false
  let retry = 250
  let reconnectTimer: NodeJS.Timeout | null = null
  let heartbeatTimer: NodeJS.Timeout | null = null
  const endpoint = new URL('/v1/desktop/capabilities', config.baseUrl)
  endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:'

  const scheduleHeartbeat = (currentSocket: SocketLike) => {
    heartbeatTimer = setTimer(() => {
      if (!stopped && socket === currentSocket && currentSocket.readyState === 1) {
        currentSocket.send(JSON.stringify({ type: 'heartbeat' }))
      }
      if (!stopped && socket === currentSocket) scheduleHeartbeat(currentSocket)
    }, 5_000)
  }
  const connect = () => {
    if (stopped) return
    const currentSocket = socketFactory(endpoint.toString())
    socket = currentSocket
    currentSocket.addEventListener('open', () => {
      if (stopped || socket !== currentSocket || currentSocket.readyState !== 1) return
      currentSocket.send(JSON.stringify({
      type: 'authenticate', token: config.deviceToken, device_id: config.deviceId
      }))
    })
    currentSocket.addEventListener('message', event => {
      void (async () => {
        try {
          if (socket !== currentSocket) return
          const payload = JSON.parse(event.data ?? '') as Record<string, unknown>
          if (payload.type === 'ready') {
            retry = 250
            if (heartbeatTimer) clearTimer(heartbeatTimer)
            scheduleHeartbeat(currentSocket)
          } else if (payload.type === 'command') {
            const result = await dispatchCapabilityCommand(manager, payload as unknown as CapabilityCommand)
            if (!stopped && socket === currentSocket && currentSocket.readyState === 1) currentSocket.send(JSON.stringify(result))
          }
        } catch { /* malformed frames never cross the desktop boundary */ }
      })()
    })
    currentSocket.addEventListener('close', () => {
      if (stopped || socket !== currentSocket) return
      reconnectTimer = setTimer(connect, retry)
      retry = Math.min(retry * 2, 5_000)
    })
    currentSocket.addEventListener('error', () => {
      if (socket === currentSocket) currentSocket.close()
    })
  }
  if (dependencies.browserReady) void dependencies.browserReady.then(connect)
  else connect()
  return () => {
    stopped = true
    if (reconnectTimer) clearTimer(reconnectTimer)
    if (heartbeatTimer) clearTimer(heartbeatTimer)
    socket?.close()
  }
}
