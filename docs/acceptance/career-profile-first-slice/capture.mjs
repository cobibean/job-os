#!/usr/bin/env node
import { writeFile } from 'node:fs/promises'

const [phase, outputDirectory, debuggerPort = '57270'] = process.argv.slice(2)
if (!phase || !outputDirectory) {
  throw new Error('Usage: capture.mjs <save|undo> <output-directory> [debugger-port]')
}

const targets = await fetch(`http://127.0.0.1:${debuggerPort}/json/list`).then((response) =>
  response.json(),
)
const target = targets.find((item) => item.type === 'page' && item.title === 'JobOS')
if (!target) throw new Error('Packaged JobOS renderer target not found')

const socket = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true })
  socket.addEventListener('error', reject, { once: true })
})

let nextId = 1
const pending = new Map()
socket.addEventListener('message', (event) => {
  const message = JSON.parse(String(event.data))
  const request = pending.get(message.id)
  if (!request) return
  pending.delete(message.id)
  if (message.error) request.reject(new Error(message.error.message))
  else request.resolve(message.result)
})

function call(method, params = {}) {
  const id = nextId++
  socket.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
}

async function evaluate(expression) {
  const result = await call('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || 'Renderer evaluation failed')
  }
  return result.result.value
}

async function waitFor(expression, timeout = 15_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(`Timed out: ${expression}`)
}

async function clickByText(selector, text) {
  const clicked = await evaluate(
    `(() => { const match=[...document.querySelectorAll(${JSON.stringify(selector)})].find(el => (el.textContent||'').includes(${JSON.stringify(text)})); if (!(match instanceof HTMLElement) || match.disabled) return false; match.click(); return true })()`,
  )
  if (!clicked) throw new Error(`Could not click ${text}`)
}

async function setValue(selector, value) {
  const changed = await evaluate(
    `(() => { const el=document.querySelector(${JSON.stringify(selector)}); if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) return false; const proto=el instanceof HTMLSelectElement?HTMLSelectElement.prototype:el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto,'value').set.call(el,${JSON.stringify(value)}); el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return true })()`,
  )
  if (!changed) throw new Error(`Could not set ${selector}`)
}

async function screenshot(filename) {
  await call('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1024,
    deviceScaleFactor: 1,
    mobile: false,
  })
  const result = await call('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
  })
  await writeFile(`${outputDirectory}/${filename}`, Buffer.from(result.data, 'base64'), {
    flag: 'wx',
    mode: 0o600,
  })
}

await call('Page.enable')
await call('Runtime.enable')
await waitFor(`document.body && document.body.innerText.includes('Review')`)

if (phase === 'save') {
  await clickByText('button', 'Career Profile')
  await waitFor(
    `document.body.innerText.includes('STAGING PROFILE') && document.body.innerText.includes('Work arrangement')`,
  )
  await setValue('select[aria-label="Work arrangement"]', 'remote')
  await setValue('select[aria-label="How important is this?"]', 'requirement')
  await setValue(
    'textarea[aria-label="Additional context"]',
    '(FAKE) Prefer roles open to applicants across the United States.',
  )
  await clickByText('button', 'Save preference')
  await waitFor(`document.body.innerText.includes('Revision 1') && document.body.innerText.includes('Saved.')`)
  await screenshot('packaged-save.png')
} else if (phase === 'undo') {
  await clickByText('button', 'Career Profile')
  await waitFor(`document.body.innerText.includes('Revision 1') && document.body.innerText.includes('Remote')`)
  await screenshot('packaged-after-restart.png')
  await setValue('select[aria-label="Work arrangement"]', 'hybrid')
  await setValue('select[aria-label="How important is this?"]', 'strong_preference')
  await setValue(
    'textarea[aria-label="Additional context"]',
    '(FAKE) Two office days per week are acceptable.',
  )
  await clickByText('button', 'Save preference')
  await waitFor(`document.body.innerText.includes('Revision 2') && document.body.innerText.includes('Saved.')`)
  await clickByText('button', 'View history')
  await waitFor(
    `document.body.innerText.includes('Work arrangement history') && document.body.innerText.includes('Revision 2')`,
  )
  await screenshot('packaged-history.png')
  const undone = await evaluate(
    `(() => { const button=document.querySelector('button[aria-label="Undo to before revision 2"]'); if (!(button instanceof HTMLButtonElement) || button.disabled) return false; button.click(); return true })()`,
  )
  if (!undone) throw new Error('Undo control unavailable')
  await waitFor(
    `document.body.innerText.includes('Revision 3') && document.body.innerText.includes('Previous preference restored as a new revision.')`,
  )
  await screenshot('packaged-undo.png')
} else {
  throw new Error(`Unknown phase: ${phase}`)
}

socket.close()
