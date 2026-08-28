import { app } from 'electron'

import { createDesktopApplication } from './app/bootstrap.js'

const desktop = createDesktopApplication()

app.whenReady().then(async () => {
  await desktop.start()
  app.on('activate', () => { void desktop.activate() })
}).catch(error => {
  console.error('[JobOS startup] Failed before the main window opened', error)
  app.exit(1)
})

app.on('before-quit', () => desktop.beforeQuit())
app.on('will-quit', () => desktop.willQuit())
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
