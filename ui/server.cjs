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

app.use(express.json())

// -- multer: save uploads to uploads_tmp inside the project
const UPLOAD_TMP = path.join(ROOT, 'uploads_tmp')
fs.mkdirSync(UPLOAD_TMP, { recursive: true })
const upload = multer({ dest: UPLOAD_TMP })

// Track running server process + its output
let serverProc = null
let serverLog = ''
const serverLogListeners = new Set()

function broadcastLog(chunk) {
  serverLog += chunk
  if (serverLog.length > 50000) serverLog = serverLog.slice(-40000)
  for (const send of serverLogListeners) {
    try { send(chunk) } catch (_) {}
  }
}

// Get local non-loopback IPv4, fall back to loopback
function getLocalIP() {
  for (const ifaces of Object.values(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) return iface.address
    }
  }
  return '127.0.0.1'
}

// ------------------------------------------------------------------ helpers

function pythonEnv() {
  return {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',   // prevent UnicodeEncodeError on Windows cp1252
    PYTHONUTF8: '1',             // Python 3.7+ UTF-8 mode
    PYTHONLEGACYWINDOWSSTDIO: '0',
  }
}

function runPython(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', args, {
      cwd: ROOT,
      env: pythonEnv(),
      ...opts,
    })
    let stdout = ''
    let stderr = ''
    if (proc.stdout) proc.stdout.on('data', d => { stdout += d })
    if (proc.stderr) proc.stderr.on('data', d => { stderr += d })
    proc.on('close', code => {
      if (code === 0) resolve({ stdout, stderr })
      else reject(new Error((stderr + stdout).trim() || `exit ${code}`))
    })
    proc.on('error', reject)
  })
}

function ensureGeneratedConfigs() {
  const serverYaml = path.join(ROOT, 'config', 'generated', 'server.yaml')
  const knockerYaml = path.join(ROOT, 'config', 'generated', 'knocker.yaml')
  if (fs.existsSync(serverYaml) && fs.existsSync(knockerYaml)) {
    return Promise.resolve({ stdout: 'configs exist', stderr: '' })
  }
  const ip = getLocalIP()
  broadcastLog(`Generating lab configs for IP ${ip}...\n`)
  return runPython([
    'scripts/generate_lab_configs.py',
    '--server-ip', ip,
    '--knocker-ip', ip,
  ])
}

// ------------------------------------------------------------------ routes

// List available encrypted videos (manifest stems)
app.get('/api/videos', (req, res) => {
  const artifactsDir = path.join(ROOT, 'artifacts')
  if (!fs.existsSync(artifactsDir)) return res.json({ videos: [] })
  const stems = fs.readdirSync(artifactsDir)
    .filter(f => f.endsWith('.manifest.json'))
    .map(f => f.replace('.manifest.json', ''))
  res.json({ videos: stems })
})

// Upload, encrypt, AND sync video in one call
app.post('/api/encrypt', upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' })

  const origName = req.file.originalname
  const stem = path.basename(origName, path.extname(origName))
  const destPath = path.join(UPLOAD_TMP, origName)
  fs.renameSync(req.file.path, destPath)

  let output = ''
  try {
    // Step 1: encrypt
    output += `\n=== Encrypting ${origName} ===\n`
    const enc = await runPython(['scripts/encrypt_video.py', destPath])
    output += enc.stdout + enc.stderr

    // Step 2: ensure configs exist
    output += `\n=== Ensuring lab configs ===\n`
    await ensureGeneratedConfigs()
    output += 'Configs ready.\n'

    // Step 3: sync video stem into configs
    output += `\n=== Syncing stem: ${stem} ===\n`
    const sync = await runPython(['scripts/sync_lab_video.py', '--stem', stem])
    output += sync.stdout + sync.stderr

    fs.unlinkSync(destPath)
    res.json({ ok: true, stem, output })
  } catch (err) {
    try { fs.unlinkSync(destPath) } catch (_) {}
    res.status(500).json({ error: err.message, output })
  }
})

// Start the lab server for a given video stem
app.post('/api/start-server', async (req, res) => {
  const { stem } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })

  // Kill existing server
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    serverProc = null
    await new Promise(r => setTimeout(r, 800))
  }

  serverLog = ''

  try {
    await ensureGeneratedConfigs()
    await runPython(['scripts/sync_lab_video.py', '--stem', stem])

    serverProc = spawn('python', [
      path.join(WRAPPER_DIR, 'server_wrapper.py'),
      '--config', 'config/generated/server.yaml',
    ], {
      cwd: ROOT,
      env: pythonEnv(),
    })

    serverProc.stdout.on('data', d => broadcastLog(d.toString()))
    serverProc.stderr.on('data', d => broadcastLog(d.toString()))

    serverProc.on('close', code => {
      broadcastLog(`\n[server process exited with code ${code}]\n`)
      serverProc = null
    })
    serverProc.on('error', err => {
      broadcastLog(`\n[server spawn error: ${err.message}]\n`)
      serverProc = null
    })

    // Wait up to 3s for server to print its ready line
    await new Promise(resolve => {
      const t = setTimeout(resolve, 3000)
      const check = setInterval(() => {
        if (serverLog.includes('Video server ready')) {
          clearTimeout(t)
          clearInterval(check)
          resolve()
        }
        if (!serverProc) {
          clearTimeout(t)
          clearInterval(check)
          resolve()
        }
      }, 200)
    })

    if (!serverProc) {
      return res.status(500).json({ error: 'Server exited immediately. Check /api/server-log.', output: serverLog })
    }

    res.json({ ok: true, pid: serverProc.pid, output: serverLog })
  } catch (err) {
    res.status(500).json({ error: err.message, output: serverLog })
  }
})

// Server status
app.get('/api/server-status', (req, res) => {
  res.json({ running: serverProc !== null && !serverProc.killed })
})

// Stop server
app.post('/api/stop-server', (req, res) => {
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    serverProc = null
  }
  res.json({ ok: true })
})

// Real-time server log via SSE
app.get('/api/server-log', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.flushHeaders()

  // Send existing log
  if (serverLog) res.write(`data: ${JSON.stringify(serverLog)}\n\n`)

  const send = chunk => res.write(`data: ${JSON.stringify(chunk)}\n\n`)
  serverLogListeners.add(send)
  req.on('close', () => serverLogListeners.delete(send))
})

// Snapshot of current server log
app.get('/api/server-log-snapshot', (req, res) => {
  res.json({ log: serverLog, running: serverProc !== null && !serverProc.killed })
})

// Play a video (runs client receiver in background)
app.post('/api/play', async (req, res) => {
  const { stem } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })

  try {
    await ensureGeneratedConfigs()
    await runPython(['scripts/sync_lab_video.py', '--stem', stem])

    const receiverProc = spawn('python', [
      path.join(WRAPPER_DIR, 'receiver_wrapper.py'),
      '--config', 'config/generated/knocker.yaml',
    ], {
      cwd: ROOT,
      env: pythonEnv(),
      detached: true,
      stdio: 'ignore',
    })
    receiverProc.unref()

    res.json({ ok: true, pid: receiverProc.pid })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Serve Vite build in production
const distPath = path.join(__dirname, 'dist')
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath))
  app.get('*', (req, res) => res.sendFile(path.join(distPath, 'index.html')))
}

app.listen(PORT, () => console.log(`NPS backend on http://localhost:${PORT}`))
