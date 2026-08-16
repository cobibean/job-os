import { createHash } from 'node:crypto'
import { lstat, readFile, realpath, writeFile } from 'node:fs/promises'
import path from 'node:path'

import type { BrowserWindow } from 'electron'

import type { DocxDocumentsService } from './docxDocuments.js'
import { mediaPrivacyViolation, parseMediaCaptureSpec, type MediaCaptureSpec } from './mediaCaptureSpec.js'

const FIXTURE_RELATIVE_PATH = 'packages/docx-engine/tests/fixtures/(FAKE)-cover-letter.docx'
const FIXTURE_SHA256 = 'e6cbea4e5185250e63f369ce0b1c7491c81547d4f2eb1783d7018a959b1ca04e'

export async function loadMediaCaptureSpec(specPath: string | undefined): Promise<MediaCaptureSpec | null> {
  if (!specPath) return null
  if (!path.isAbsolute(specPath)) throw new Error('Media capture spec path must be absolute')
  const metadata = await lstat(specPath)
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size > 128_000) {
    throw new Error('Invalid media capture spec file')
  }
  return parseMediaCaptureSpec(JSON.parse(await readFile(specPath, 'utf8')))
}

export async function bindMediaFixture(
  service: DocxDocumentsService,
  sourceRoot: string
): Promise<void> {
  const fixture = path.join(sourceRoot, FIXTURE_RELATIVE_PATH)
  const fixtureMetadata = await lstat(fixture)
  if (!fixtureMetadata.isFile() || fixtureMetadata.isSymbolicLink() || await realpath(fixture) !== fixture) {
    throw new Error('Approved synthetic media fixture path is invalid')
  }
  const bytes = await readFile(fixture)
  if (createHash('sha256').update(bytes).digest('hex') !== FIXTURE_SHA256) {
    throw new Error('Approved synthetic media fixture checksum mismatch')
  }
  await service.openArtifact('jobos-demo-v1', 'cover_letter', {
    filename: '(FAKE)-cover-letter.docx',
    sha256: FIXTURE_SHA256,
    bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
  })
}

async function safeOutputDirectory(outputDirectory: string): Promise<string> {
  const metadata = await lstat(outputDirectory)
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error('Invalid media capture output directory')
  const canonical = await realpath(outputDirectory)
  if (canonical !== path.resolve(outputDirectory)) throw new Error('Media capture output directory must be canonical')
  return canonical
}

async function rendererAction(window: BrowserWindow, action: Extract<MediaCaptureSpec['actions'][number], { kind: 'wait' | 'click' }>) {
  const source = `(() => {
    const selector = ${JSON.stringify(action.selector)};
    const expected = ${JSON.stringify(action.text ?? null)};
    const deadline = Date.now() + ${action.timeoutMs};
    return new Promise((resolve, reject) => {
      const check = () => {
        const matches = Array.from(document.querySelectorAll(selector));
        const element = matches.find(item => {
          const style = getComputedStyle(item);
          const visible = style.display !== 'none' && style.visibility !== 'hidden' && item.getClientRects().length > 0;
          return visible && (expected === null || (item.textContent || '').includes(expected));
        });
        if (element) {
          if (${JSON.stringify(action.kind)} === 'click') {
            if (!(element instanceof HTMLElement)) return reject(new Error('Capture target is not interactive'));
            if ('disabled' in element && element.disabled) return reject(new Error('Capture target is disabled'));
            element.click();
          }
          return resolve(true);
        }
        if (Date.now() >= deadline) return reject(new Error('Capture state timed out'));
        setTimeout(check, 50);
      };
      check();
    });
  })()`
  await window.webContents.executeJavaScript(source, true)
}

async function applyCaptureStyle(window: BrowserWindow): Promise<void> {
  await window.webContents.executeJavaScript(`(() => {
    const existing = document.querySelector('style[data-jobos-media-capture]');
    const style = existing || document.createElement('style');
    style.dataset.jobosMediaCapture = 'true';
    style.textContent = '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}.docx-recovery-panel{display:none!important}';
    if (!existing) document.head.appendChild(style);
    document.querySelectorAll('.docx-recovery-panel').forEach(element => {
      if (element instanceof HTMLElement) element.style.setProperty('display', 'none', 'important');
    });
  })()`, true)
}

async function capture(window: BrowserWindow, outputDirectory: string, filename: string): Promise<void> {
  await applyCaptureStyle(window)
  const target = path.join(outputDirectory, filename)
  if (path.dirname(target) !== outputDirectory) throw new Error('Media capture path escaped output directory')
  const visibleText = await window.webContents.executeJavaScript(`(() => {
    const values = Array.from(document.querySelectorAll('input,textarea')).map(element => element.value || '');
    return [document.body.innerText, document.title, ...values].join('\\n');
  })()`, true) as string
  const privacyError = mediaPrivacyViolation(visibleText)
  if (privacyError) throw new Error(`${filename}: ${privacyError}`)
  const image = await window.webContents.capturePage()
  const size = image.getSize()
  const expectedWidth = 1440
  const expectedHeight = 1024
  const expectedAspectRatio = expectedWidth / expectedHeight
  if (
    size.width < expectedWidth
    || size.height < expectedHeight
    || Math.abs(size.width / size.height - expectedAspectRatio) > 0.001
  ) throw new Error(`Media capture dimensions changed: ${size.width}x${size.height}`)
  const normalized = size.width === expectedWidth && size.height === expectedHeight
    ? image
    : image.resize({ width: expectedWidth, height: expectedHeight, quality: 'best' })
  const normalizedSize = normalized.getSize()
  if (normalizedSize.width !== expectedWidth || normalizedSize.height !== expectedHeight) {
    throw new Error('Media capture normalization failed')
  }
  await writeFile(target, normalized.toPNG(), { flag: 'wx', mode: 0o600 })
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

export async function runMediaCapture(window: BrowserWindow, spec: MediaCaptureSpec): Promise<void> {
  const outputDirectory = await safeOutputDirectory(spec.outputDirectory)
  await applyCaptureStyle(window)
  for (const action of spec.actions) {
    if (action.kind === 'wait' || action.kind === 'click') {
      await rendererAction(window, action)
    } else if (action.kind === 'capture') {
      await capture(window, outputDirectory, action.filename)
    } else {
      for (let index = 0; index < action.count; index += 1) {
        await capture(window, outputDirectory, `${action.prefix}${String(action.start + index).padStart(4, '0')}.png`)
        if (index + 1 < action.count) await delay(action.intervalMs)
      }
    }
  }
}
