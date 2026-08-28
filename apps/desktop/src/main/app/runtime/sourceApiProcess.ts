import { execFile, spawn } from 'node:child_process'
import path from 'node:path'
import type { ChildProcess } from 'node:child_process'

import type { DesktopRuntimeConfig } from './runtimeConfig.js'

export function createSourceApiProcess(dependencies: {
  sourceRoot: string
  isPackaged: () => boolean
  getConfigPath: () => string | null
  environment: NodeJS.ProcessEnv
}) {
  let child: ChildProcess | null = null

  const stop = async (): Promise<void> => {
    const active = child
    child = null
    if (!active || active.exitCode !== null) return
    const exited = () => new Promise<void>(resolve => active.once('exit', () => resolve()))
    active.kill()
    await Promise.race([exited(), new Promise<void>(resolve => setTimeout(resolve, 2_000))])
    if (active.exitCode === null) {
      active.kill('SIGKILL')
      await Promise.race([exited(), new Promise<void>(resolve => setTimeout(resolve, 2_000))])
    }
    if (active.exitCode === null) throw new Error('Source JobOS API did not stop')
  }

  return {
    start(runtime: DesktopRuntimeConfig): Promise<void> {
      const configPath = dependencies.getConfigPath()
      if (dependencies.isPackaged() || !configPath) return Promise.reject(new Error('Source API startup is unavailable'))
      if (child && child.exitCode === null) return Promise.resolve()
      const address = new URL(runtime.apiBaseUrl)
      const uvExecutable = dependencies.environment.JOBOS_UV_EXECUTABLE ?? 'uv'
      const childEnvironment = { ...dependencies.environment }
      delete childEnvironment.JOBOS_DEVICE_TOKEN
      delete childEnvironment.JOBOS_MCP_TOKEN
      return new Promise((resolve, reject) => {
        const started = spawn(uvExecutable, ['run', 'uvicorn', 'jobos_api.main:app', '--host', address.hostname, '--port', address.port || '8766'], {
          cwd: dependencies.sourceRoot,
          env: { ...childEnvironment, JOBOS_CONFIG_PATH: configPath, JOBOS_TRANSPORT: runtime.mode === 'remote-client' ? 'private-remote' : 'local-loopback' },
          stdio: 'ignore'
        })
        started.once('spawn', () => { child = started; resolve() })
        started.once('error', error => { if (child === started) child = null; reject(error) })
        started.once('exit', () => { if (child === started) child = null })
      })
    },
    stop,
    dispose(): void {
      child?.kill()
      child = null
    },
    runInitializer(arguments_: string[]): Promise<void> {
      const override = dependencies.environment.JOBOS_INIT_EXECUTABLE
      const executable = override ?? 'uv'
      const commandArguments = override ? arguments_ : ['run', 'jobos-init', ...arguments_]
      if (dependencies.isPackaged() && !override) return Promise.reject(new Error('Packaged setup executable is unavailable'))
      return new Promise((resolve, reject) => {
        execFile(executable, commandArguments, { cwd: dependencies.sourceRoot, encoding: 'utf8', maxBuffer: 8192, timeout: 30_000 }, error => (
          error ? reject(new Error('Local setup command failed')) : resolve()
        ))
      })
    },
    rollbackProfileSwitch(switchId: string): Promise<void> {
      const configPath = dependencies.getConfigPath()
      if (!configPath || dependencies.isPackaged()) return Promise.reject(new Error('Source JobOS Profile rollback is unavailable'))
      const uvExecutable = dependencies.environment.JOBOS_UV_EXECUTABLE ?? 'uv'
      return new Promise((resolve, reject) => {
        execFile(uvExecutable, ['run', 'python', '-m', 'jobos_api.macos_runtime', 'source-profile-rollback', '--registry', path.join(path.dirname(configPath), 'installation-profiles.json'), '--switch-id', switchId], {
          cwd: dependencies.sourceRoot, encoding: 'utf8', maxBuffer: 8192, timeout: 10_000
        }, error => error ? reject(new Error('Source JobOS Profile rollback failed')) : resolve())
      })
    }
  }
}
