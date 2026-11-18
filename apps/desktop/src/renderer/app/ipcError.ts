/** Strip Electron's "Error invoking remote method '…': Error:" wrapper so only the app's own message is shown. */
export function ipcErrorMessage(failure: unknown, fallback: string): string {
  if (!(failure instanceof Error)) return fallback
  const message = failure.message.replace(
    /^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,
    ''
  ).trim()
  return message || fallback
}
