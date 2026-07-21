import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entrypoint = path.join(desktopRoot, 'dist/preload/preload.cjs')
const source = await readFile(entrypoint, 'utf8')
const localRequirePattern = /\brequire\s*\(\s*(['"])(?:\.\.?\/)[^'"]*\1\s*\)/g
const localRequires = [...source.matchAll(localRequirePattern)].map(match => match[0])

assert.deepEqual(
  localRequires,
  [],
  `Sandboxed production preload must be self-contained; found local runtime requires: ${localRequires.join(', ')}`
)

const invokeCalls = []
const listeners = new Map()
const removedListeners = []
const exposures = []
const ipcRenderer = {
  invoke(channel, ...args) {
    invokeCalls.push([channel, ...args])
    return Promise.resolve({})
  },
  on(channel, listener) {
    listeners.set(channel, listener)
    return ipcRenderer
  },
  removeListener(channel, listener) {
    removedListeners.push([channel, listener])
    return ipcRenderer
  }
}
const electron = {
  contextBridge: {
    exposeInMainWorld(name, bridge) {
      exposures.push([name, bridge])
    }
  },
  ipcRenderer
}
const customRequire = specifier => {
  assert.equal(specifier, 'electron', `Sandboxed preload attempted unsupported runtime require: ${specifier}`)
  return electron
}

vm.runInNewContext(source, {
  exports: {},
  module: { exports: {} },
  require: customRequire
}, { filename: entrypoint })

assert.equal(exposures.length, 1, 'Preload must expose exactly one renderer bridge')
const [worldName, bridge] = exposures[0]
assert.equal(worldName, 'jobos')
assert.equal(typeof bridge, 'object')
assert.equal(Object.isFrozen(bridge.agent), true, 'bridge.agent must be frozen')
assert.deepEqual(
  Object.keys(bridge.agent).sort(),
  ['cancel', 'get', 'reset', 'retry', 'send', 'subscribe'],
  'bridge.agent must expose only fixed methods'
)

await bridge.agent.get()
await bridge.agent.reset()
await bridge.agent.send('Hello', 'idempotency-0001')
await bridge.agent.cancel('turn-1')
await bridge.agent.retry('turn-1', 'idempotency-0002')
assert.deepEqual(invokeCalls, [
  ['jobos:agent:get'],
  ['jobos:agent:reset'],
  ['jobos:agent:send', 'Hello', 'idempotency-0001'],
  ['jobos:agent:cancel', 'turn-1'],
  ['jobos:agent:retry', 'turn-1', 'idempotency-0002']
])

const received = []
const unsubscribe = bridge.agent.subscribe(update => received.push(update))
const streamListener = listeners.get('jobos:agent:event')
assert.equal(typeof streamListener, 'function')
const update = { kind: 'connection', state: 'reconnecting' }
streamListener({}, update)
assert.deepEqual(received, [update])
unsubscribe()
assert.deepEqual(removedListeners, [['jobos:agent:event', streamListener]])
