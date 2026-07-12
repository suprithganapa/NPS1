const express = require('express')
const multer = require('multer')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const os = require('os')

const app = express()
const PORT = 3001
const ROOT = path.resolve(__dirname, '..')
const WRAPPER_DIR = __dirname
const GENERATED_DIR = path.join(ROOT, 'config', 'generated')

app.use(express.json())

const UPLOAD_TMP = path.join(ROOT, 'uploads_tmp')
fs.mkdirSync(UPLOAD_TMP, { recursive: true })
const upload = multer({ dest: UPLOAD_TMP })

let serverProc = null
let serverLog = ''
let currentStem = null
const serverLogListeners = new Set()

function broadcastLog(chunk) {
  serverLog += chunk
  if (serverLog.length > 80000) serverLog = serverLog.slice(-60000)
  for (const send of serverLogListeners) {
    try { send(chunk) } catch (_) {}
  }
}

function getLocalIP() {
  for (const ifaces of Object.values(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) return iface.address
    }
  }
  return '127.0.0.1'
}

function pythonEnv() {
  return {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    PYTHONLEGACYWINDOWSSTDIO: '0',
  }
}

function runPython(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', args, { cwd: ROOT, env: pythonEnv(), ...opts })
    let out = '', err = ''
    if (proc.stdout) proc.stdout.on('data', d => { out += d })
    if (proc.stderr) proc.stderr.on('data', d => { err += d })
    proc.on('close', code => code === 0
      ? resolve({ stdout: out, stderr: err })
      : reject(new Error((err + out).trim() || `exit ${code}`)))
    proc.on('error', reject)
  })
}

// Generate configs only if they don't exist yet — never touch TOTP or anything else
function ensureConfigs() {
  const s = path.join(GENERATED_DIR, 'server.yaml')
  const k = path.join(GENERATED_DIR, 'knocker.yaml')
  if (fs.existsSync(s) && fs.existsSync(k)) return Promise.resolve()
  const ip = getLocalIP()
  broadcastLog(`Generating lab configs (IP=${ip})\n`)
  return runPython([
    'scripts/generate_lab_configs.py',
    '--server-ip', ip,
    '--knocker-ip', ip,
  ])
}

async function killServer() {
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    await new Promise(r => setTimeout(r, 1000))
    serverProc = null
  }
}

// Only sync_lab_video.py is called to update configs — it only changes the video: section
async function startServer(stem) {
  await killServer()
  serverLog = ''
  currentStem = stem

  await ensureConfigs()
  await runPython(['scripts/sync_lab_video.py', '--stem', stem])

  serverProc = spawn('python', [
    path.join(WRAPPER_DIR, 'server_wrapper.py'),
    '--config', 'config/generated/server.yaml',
  ], { cwd: ROOT, env: pythonEnv() })

  serverProc.stdout.on('data', d => broadcastLog(d.toString()))
  serverProc.stderr.on('data', d => broadcastLog(d.toString()))
  serverProc.on('close', code => {
    broadcastLog(`\n[server exited: code ${code}]\n`)
    serverProc = null
  })
  serverProc.on('error', err => {
    broadcastLog(`\n[server error: ${err.message}]\n`)
    serverProc = null
  })

  // Wait up to 5s for server ready line
  await new Promise(resolve => {
    const deadline = setTimeout(resolve, 5000)
    const poll = setInterval(() => {
      if (serverLog.includes('Video server ready') || !serverProc) {
        clearTimeout(deadline); clearInterval(poll); resolve()
      }
    }, 200)
  })

  if (!serverProc) throw new Error('Server exited immediately — check server output panel')
}

// ------------------------------------------------------------------ API

app.get('/api/videos', (req, res) => {
  const dir = path.join(ROOT, 'artifacts')
  if (!fs.existsSync(dir)) return res.json({ videos: [] })
  const stems = fs.readdirSync(dir)
    .filter(f => f.endsWith('.manifest.json'))
    .map(f => f.replace('.manifest.json', ''))
  res.json({ videos: stems })
})

app.get('/api/server-status', (req, res) => {
  res.json({ running: serverProc !== null && !serverProc.killed, stem: currentStem })
})

// Upload + encrypt (step 1) then sync video: paths only (step 2)
app.post('/api/encrypt', upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' })
  const origName = req.file.originalname
  const stem = path.basename(origName, path.extname(origName))
  const destPath = path.join(UPLOAD_TMP, origName)
  fs.renameSync(req.file.path, destPath)

  let output = ''
  try {
    output += `=== Step 1: Encrypting ${origName} ===\n`
    const enc = await runPython(['scripts/encrypt_video.py', destPath])
    output += enc.stdout + enc.stderr

    output += `\n=== Step 2: Syncing video path in configs ===\n`
    await ensureConfigs()
    const sync = await runPython(['scripts/sync_lab_video.py', '--stem', stem])
    output += sync.stdout + sync.stderr
    output += `\nReady. "${stem}" can now be served.\n`

    try { fs.unlinkSync(destPath) } catch (_) {}
    res.json({ ok: true, stem, output })
  } catch (err) {
    try { fs.unlinkSync(destPath) } catch (_) {}
    res.status(500).json({ error: err.message, output })
  }
})

// Start server manually from server page
app.post('/api/start-server', async (req, res) => {
  const { stem } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })
  try {
    await startServer(stem)
    res.json({ ok: true, pid: serverProc?.pid, stem })
  } catch (err) {
    res.status(500).json({ error: err.message, output: serverLog })
  }
})

app.post('/api/stop-server', async (req, res) => {
  await killServer()
  currentStem = null
  res.json({ ok: true })
})

// SSE: real-time server output stream
app.get('/api/server-log', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.flushHeaders()
  if (serverLog) res.write(`data: ${JSON.stringify(serverLog)}\n\n`)
  const send = chunk => res.write(`data: ${JSON.stringify(chunk)}\n\n`)
  serverLogListeners.add(send)
  req.on('close', () => serverLogListeners.delete(send))
})

// Play: stop server → sync video: section only → restart → launch receiver
app.post('/api/play', async (req, res) => {
  const { stem } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })

  const manifest = path.join(ROOT, 'artifacts', `${stem}.manifest.json`)
  const segkeys  = path.join(ROOT, 'server_secrets', `${stem}.segkeys`)
  if (!fs.existsSync(manifest))
    return res.status(404).json({ error: `${stem}.manifest.json not found` })
  if (!fs.existsSync(segkeys))
    return res.status(404).json({ error: `${stem}.segkeys not found` })

  try {
    if (currentStem !== stem || !serverProc || serverProc.killed) {
      broadcastLog(`\n=== Switching to: ${stem} ===\n`)
      await startServer(stem)
    }

    const receiverProc = spawn('python', [
      path.join(WRAPPER_DIR, 'receiver_wrapper.py'),
      '--config', 'config/generated/knocker.yaml',
    ], { cwd: ROOT, env: pythonEnv(), detached: true, stdio: 'ignore' })
    receiverProc.unref()

    res.json({ ok: true, stem, serverPid: serverProc?.pid, receiverPid: receiverProc.pid })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

const distPath = path.join(__dirname, 'dist')
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath))
  app.get('*', (req, res) => res.sendFile(path.join(distPath, 'index.html')))
}

app.listen(PORT, () => console.log(`NPS backend on http://localhost:${PORT}`))
