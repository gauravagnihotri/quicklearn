// Copies the Vite build output into the Python package as `quicklearn/static/`.
//
// The backend looks there first (see `backend/quicklearn/config.py`, `Config.static_dir`) and
// only falls back to `frontend/dist/` for a source checkout. Without this copy a non-editable
// `pip install .` — and any Docker image — serves the API with no UI, because the fallback
// path resolves inside site-packages.
//
// Plain Node with no dependencies so it behaves the same on Windows, macOS and Linux.

import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const dist = resolve(here, '..', 'dist')
const target = resolve(here, '..', '..', 'backend', 'quicklearn', 'static')

if (!existsSync(dist)) {
  console.error(`copy-static: no build output at ${dist} — run \`vite build\` first.`)
  process.exit(1)
}

// Remove first, so a renamed or deleted asset does not linger from an earlier build.
rmSync(target, { recursive: true, force: true })
mkdirSync(target, { recursive: true })
cpSync(dist, target, { recursive: true })

console.log(`copy-static: ${dist} -> ${target}`)
