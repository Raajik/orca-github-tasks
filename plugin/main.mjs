// Worker for the "Refresh now" command: reruns the generator, which rewrites
// panel.html; Orca's dev-plugin watcher then reloads the panel.
import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const generator = fileURLToPath(new URL('../bin/generate.py', import.meta.url))

export default function activate(orca) {
  orca.commands.register('refresh', () =>
    new Promise((resolve) => {
      execFile('/usr/bin/python3', [generator], { timeout: 120_000 }, (error, stdout, stderr) => {
        if (error) orca.log(`refresh failed: ${stderr || error.message}`)
        resolve({ ok: !error, output: String(stdout).trim() })
      })
    })
  )
}
