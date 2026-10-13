import { useEffect, useState, useRef } from 'react'

const API_URL = import.meta.env.VITE_API_URL || ''
const WS_URL = API_URL
  ? API_URL.replace(/^http/, 'ws')
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.hostname}:8000`

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).filter(Boolean).join(' ')
      : data.detail
    throw new Error(detail || 'Une erreur est survenue.')
  }
  return data
}

function Logo() {
  return (
    <a className="logo" href="#top" aria-label="Pledge accueil">
      <img src="/logo.png" alt="Pledge Stream Beyond" style={{height: '35px', objectFit: 'contain'}} />
    </a>
  )
}

function PlayIcon() {
  return <span className="play-icon" aria-hidden="true">▶</span>
}

function AuthDialog({ mode, setMode, onSuccess, onClose }) {
  const isRegister = mode === 'register'
  const [form, setForm] = useState({ email: '', password: '', role: 'viewer' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  function updateField(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      const payload = isRegister ? form : { email: form.email, password: form.password }
      let data = await apiRequest(isRegister ? '/auth/register' : '/auth/login', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      if (isRegister) {
        data = await apiRequest('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email: form.email, password: form.password }),
        })
      }
      onSuccess(data.user || form)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button className="close-button" type="button" onClick={onClose} aria-label="Fermer">×</button>
        <span className="eyebrow">{isRegister ? 'Rejoindre Pledge' : 'Bon retour'}</span>
        <h2 id="auth-title">{isRegister ? 'Créez votre compte' : 'Se connecter'}</h2>
        <p>{isRegister ? 'Lancez votre expérience de streaming sans limites.' : 'Retrouvez votre espace Pledge et vos contenus.'}</p>
        <form onSubmit={handleSubmit}>
          <label>Adresse email<input name="email" type="email" value={form.email} onChange={updateField} placeholder="vous@exemple.com" autoComplete="email" required /></label>
          <label>Mot de passe<input name="password" type="password" value={form.password} onChange={updateField} placeholder="••••••••" minLength="6" autoComplete={isRegister ? 'new-password' : 'current-password'} required /></label>
          {isRegister && <label>Type de compte<select name="role" value={form.role} onChange={updateField}><option value="viewer">Viewer</option><option value="streamer">Streamer</option></select></label>}
          <div className="form-error" role="alert">{error}</div>
          <button className="button button-primary button-wide" type="submit" disabled={loading}>{loading ? 'Connexion...' : isRegister ? 'Créer mon compte' : 'Se connecter'}</button>
        </form>
        <button className="text-button" type="button" onClick={() => setMode(isRegister ? 'login' : 'register')}>{isRegister ? 'J’ai déjà un compte' : 'Créer un compte'}</button>
      </section>
    </div>
  )
}

function ActiveStreamsList({ onSelectStream }) {
  const [streams, setStreams] = useState([])
  
  useEffect(() => {
    function fetchStreams() {
      apiRequest('/api/streams/active')
        .then(data => {
          if (data && Array.isArray(data.streams)) setStreams(data.streams)
        })
        .catch(() => {})
    }
    fetchStreams()
    const interval = setInterval(fetchStreams, 3000)
    return () => clearInterval(interval)
  }, [])

  if (streams.length === 0) return <p style={{color: '#888'}}>Aucun flux en direct actuellement.</p>

  return (
    <div style={{display: 'flex', gap: '1rem', flexWrap: 'wrap'}}>
      {streams.map(s => (
        <button key={s} className="button button-quiet" onClick={() => onSelectStream(s)}>
          <span style={{color: '#70e8b3', marginRight: '8px'}}>●</span> {s}
        </button>
      ))}
    </div>
  )
}

function Home({ openAuth }) {
  return (
    <section className="hero" id="top" style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', textAlign: 'center', minHeight: '75vh'}}>
      <div className="hero-copy" style={{maxWidth: '800px', margin: '0 auto'}}>
        <span className="eyebrow">Play + Edge</span>
        <h1>Streaming Edge<br /><em>Nouvelle génération.</em></h1>
        <p>Un réseau décentralisé. Une sécurité cryptographique. Une expérience ultra-fluide.</p>
        <div className="hero-actions" style={{justifyContent: 'center', marginTop: '40px'}}>
          <button className="button button-primary" onClick={() => openAuth('register')}>Rejoindre le réseau <span>→</span></button>
          <button className="button button-quiet" onClick={() => openAuth('login')}>Se connecter</button>
        </div>
        <div style={{marginTop: '60px'}}>
          <h3 style={{marginBottom: '1rem', color: '#8899b8', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '2px'}}>En direct sur Pledge</h3>
          <div style={{display: 'flex', justifyContent: 'center'}}>
            <ActiveStreamsList onSelectStream={(s) => openAuth('login')} />
          </div>
        </div>
      </div>
    </section>
  )
}

function StreamerInterface({ user }) {
  const [streaming, setStreaming] = useState(false)
  const [chunksSent, setChunksSent] = useState(0)
  const [ws, setWs] = useState(null)
  const [mediaRecorder, setMediaRecorder] = useState(null)
  const videoRef = useRef()

  async function startStream() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      if (videoRef.current) videoRef.current.srcObject = stream
      
      const socket = new WebSocket(`${WS_URL}/api/stream/publish/${user.email}`)
      socket.onopen = () => {
        setStreaming(true)
        setChunksSent(0)
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
        const recorder = new MediaRecorder(stream, { mimeType: mime })
        
        recorder.ondataavailable = async (e) => {
          if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) {
            socket.send(await e.data.arrayBuffer())
            setChunksSent(c => c + 1)
          }
        }
        // Cadence basse latence à 600ms
        recorder.start(600)
        setMediaRecorder(recorder)
      }
      
      socket.onclose = (e) => {
        console.log('Connexion WSS fermée', e)
        setStreaming(false)
        if (e.code !== 1000 && e.code !== 1005) {
          alert(`La diffusion s'est arrêtée : ${e.reason || 'Erreur réseau'}`)
        }
        if (videoRef.current && videoRef.current.srcObject) {
          videoRef.current.srcObject.getTracks().forEach(t => t.stop())
          videoRef.current.srcObject = null
        }
      }
      
      setWs(socket)
    } catch (e) {
      console.error('Erreur caméra:', e)
      alert("Erreur d'accès à la caméra")
    }
  }

  function stopStream() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
    if (ws) ws.close()
    setStreaming(false)
    if (videoRef.current && videoRef.current.srcObject) {
      videoRef.current.srcObject.getTracks().forEach(t => t.stop())
      videoRef.current.srcObject = null
    }
  }

  return (
    <article className="dashboard-card stream-card">
      <div className="card-heading">
        <div><span className="eyebrow">Studio Edge</span><h2>Votre diffusion</h2></div>
        <span className="connection-badge"><i style={{background: streaming ? '#22c55e' : '#ef4444'}} /> {streaming ? `En ligne (${chunksSent} blocs)` : 'Hors ligne'}</span>
      </div>
      <video ref={videoRef} autoPlay muted style={{width: '100%', background: '#000', borderRadius: '12px', minHeight: '200px'}} />
      <div style={{marginTop: '1rem'}}>
        {streaming ? (
          <button className="button button-quiet" onClick={stopStream}>Arrêter la diffusion</button>
        ) : (
          <button className="button button-primary" onClick={startStream}>Démarrer la caméra</button>
        )}
      </div>
    </article>
  )
}

function ViewerInterface({ user }) {
  const [streamId, setStreamId] = useState('')
  const [watching, setWatching] = useState(false)
  const [chunksReceived, setChunksReceived] = useState(0)
  const videoRef = useRef()
  const [ws, setWs] = useState(null)
  
  function startWatching(idToWatch) {
    const targetId = idToWatch || streamId
    if (!targetId) return
    setWatching(true)
    setChunksReceived(0)
    
    const socket = new WebSocket(`${WS_URL}/api/stream/watch/${targetId}`)
    socket.binaryType = "arraybuffer"
    
    const mediaSource = new MediaSource()
    setTimeout(() => {
      if (videoRef.current) {
        videoRef.current.src = URL.createObjectURL(mediaSource)
      }
    }, 50)
    
    mediaSource.addEventListener('sourceopen', () => {
      const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
      let sourceBuffer
      try {
        sourceBuffer = mediaSource.addSourceBuffer(mime)
      } catch (err) {
        console.error("Erreur addSourceBuffer:", err)
        return
      }

      const queue = []
      
      function processQueue() {
        if (queue.length > 0 && sourceBuffer && !sourceBuffer.updating) {
          const next = queue.shift()
          try {
            sourceBuffer.appendBuffer(next)
          } catch (e) {
            console.warn("Erreur buffer append:", e)
          }
        }
      }

      socket.onmessage = (event) => {
        const chunk = new Uint8Array(event.data)
        setChunksReceived(c => c + 1)
        if (sourceBuffer && !sourceBuffer.updating && queue.length === 0) {
          try {
            sourceBuffer.appendBuffer(chunk)
          } catch (e) {
            queue.push(chunk)
          }
        } else {
          // Limite de taille de file d'attente pour éviter la saturation mémoire
          if (queue.length < 50) {
            queue.push(chunk)
          }
        }

        // Tentative de lecture automatique
        if (videoRef.current && videoRef.current.paused) {
          videoRef.current.play().catch(() => {
            if (videoRef.current) {
              videoRef.current.muted = true
              videoRef.current.play().catch(() => {})
            }
          })
        }
      }
      
      sourceBuffer.addEventListener('updateend', processQueue)
    })
    
    socket.onclose = (e) => {
      console.log('Connexion WSS fermée', e)
      setWatching(false)
    }
    
    setWs(socket)
  }

  function stopWatching() {
    if (ws) ws.close()
    setWatching(false)
  }

  return (
    <article className="dashboard-card stream-card">
      <div className="card-heading">
        <div><span className="eyebrow">Lecteur Edge</span><h2>Regarder un flux</h2></div>
        {watching && <span className="connection-badge"><i style={{background: '#22c55e'}} /> Direct ({chunksReceived} blocs)</span>}
      </div>
      {!watching ? (
        <div style={{marginTop: '1rem'}}>
          <h3 style={{marginBottom: '1rem', fontSize: '14px'}}>Flux en direct</h3>
          <ActiveStreamsList onSelectStream={(s) => { setStreamId(s); startWatching(s) }} />
        </div>
      ) : (
        <>
          <video ref={videoRef} autoPlay controls playsInline style={{width: '100%', background: '#000', borderRadius: '12px', minHeight: '200px', marginTop: '1rem'}} />
          <div style={{marginTop: '1rem'}}>
            <button className="button button-quiet" onClick={stopWatching}>Arrêter de regarder</button>
          </div>
        </>
      )}
    </article>
  )
}

function AdminDashboard({ user }) {
  const [activeTab, setActiveTab] = useState('docker')
  const [containers, setContainers] = useState([])
  const [nodesData, setNodesData] = useState({})
  const [logs, setLogs] = useState(null)
  const [loadingLogs, setLoadingLogs] = useState(false)
  const logsEndRef = useRef(null)

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  useEffect(() => {
    if (activeTab === 'docker') {
      apiRequest('/api/admin/containers').then(data => {
        if (!data.error) setContainers(data)
        else console.error(data.error)
      })
    } else {
      apiRequest('/api/admin/nodes').then(data => {
        if (!data.error) setNodesData(data.zones || {})
      })
    }
  }, [activeTab])

  async function fetchLogs(containerName) {
    setLoadingLogs(true)
    setLogs(null)
    const data = await apiRequest(`/api/admin/containers/${containerName}/logs`)
    setLogs(data.logs || data.error)
    setLoadingLogs(false)
  }

  return (
    <div style={{gridColumn: '1 / -1'}}>
      <div style={{display: 'flex', gap: '1rem', marginBottom: '2rem'}}>
        <button className={activeTab === 'docker' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('docker')}>Conteneurs Docker</button>
        <button className={activeTab === 'nodes' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('nodes')}>Zones & Nœuds Edge</button>
        <button className={activeTab === 'streams' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('streams')}>Flux Vidéos</button>
      </div>

      {activeTab === 'docker' && (
        <div style={{display: 'flex', gap: '2rem'}}>
          <div style={{flex: 1}}>
            <h3 style={{marginBottom: '1rem'}}>Conteneurs en cours</h3>
            {containers.length === 0 ? <p>Aucun conteneur ou Docker inactif.</p> : (
              <div style={{display: 'flex', flexDirection: 'column', gap: '1rem'}}>
                {containers.map(c => (
                  <div key={c.id} style={{background: '#111', padding: '1rem', borderRadius: '8px', border: '1px solid #333', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                    <div>
                      <strong>{c.name}</strong><br />
                      <small style={{color: '#888'}}>{c.image} - <span style={{color: c.status.includes('Up') ? '#22c55e' : '#ef4444'}}>{c.status}</span></small>
                    </div>
                    <button className="button button-quiet button-small" onClick={() => fetchLogs(c.name)}>Logs</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{flex: 1, background: '#0a0a0a', border: '1px solid #222', borderRadius: '8px', padding: '1rem', maxHeight: '600px', overflowY: 'auto'}}>
            <h3 style={{marginBottom: '1rem'}}>Logs Console</h3>
            {loadingLogs ? <p>Chargement...</p> : (
              <pre style={{fontSize: '0.8rem', whiteSpace: 'pre-wrap', color: '#00ff00', fontFamily: 'monospace'}}>
                {logs || "Sélectionnez un conteneur pour voir ses logs."}
                <div ref={logsEndRef} />
              </pre>
            )}
          </div>
        </div>
      )}

      {activeTab === 'nodes' && (
        <div>
          <h3 style={{marginBottom: '1rem'}}>Topologie du Réseau Edge</h3>
          {Object.keys(nodesData).length === 0 ? <p>Aucune zone ou noeud détecté.</p> : (
            <div style={{display: 'flex', flexDirection: 'column', gap: '2rem'}}>
              {Object.entries(nodesData).map(([zoneId, nodes]) => (
                <div key={zoneId} style={{background: '#111', padding: '1.5rem', borderRadius: '12px', border: '1px solid #333'}}>
                  <h4 style={{marginBottom: '1rem', color: '#60a5fa'}}>{zoneId}</h4>
                  <div className="catalog-grid" style={{gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))'}}>
                    {nodes.map(n => (
                      <div key={n.node_id} style={{background: '#000', padding: '1rem', borderRadius: '8px', border: '1px solid #222'}}>
                        <h5 style={{marginBottom: '0.5rem'}}>{n.node_id}</h5>
                        <ul style={{listStyle: 'none', padding: 0, margin: 0, fontSize: '0.9rem', color: '#aaa'}}>
                          <li><strong>IP:</strong> {n.ip_address}:{n.wss_port}</li>
                          <li><strong>Stockage:</strong> {n.storage_available_gb} Go</li>
                          <li><strong>CPU:</strong> {n.cpu_usage_pct}%</li>
                          <li><strong>Vu le:</strong> {n.last_seen ? new Date(n.last_seen).toLocaleTimeString() : 'N/A'}</li>
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'streams' && (
        <div>
          <h3 style={{marginBottom: '1rem'}}>Flux Vidéos en cours de diffusion</h3>
          <ActiveStreamsList onSelectStream={() => {}} />
        </div>
      )}
    </div>
  )
}

function Dashboard({ user, onLogout }) {
  return (
    <section className="dashboard">
      <div className="dashboard-grid">
        {user.role === 'admin' ? <AdminDashboard user={user} /> : user.role === 'streamer' ? <StreamerInterface user={user} /> : <ViewerInterface user={user} />}
        {user.role !== 'admin' && (
          <aside className="dashboard-card profile-card">
          <span className="avatar">{user.email?.charAt(0).toUpperCase()}</span>
          <span className="eyebrow">Votre profil</span>
          <h2>{user.email}</h2>
          <dl>
            <div><dt>Rôle</dt><dd>{user.role || 'viewer'}</dd></div>
            <div><dt>Statut</dt><dd className="green">Actif</dd></div>
          </dl>
        </aside>
        )}
      </div>
    </section>
  )
}

export default function App() {
  const [authMode, setAuthMode] = useState(null)
  const [user, setUser] = useState(null)

  useEffect(() => {
    apiRequest('/auth/me')
      .then(data => { if (data.user) setUser(data.user) })
      .catch(() => {}) // Ignore errors if not logged in
  }, [])

  async function logout() { try { await apiRequest('/auth/logout', { method: 'POST' }) } finally { setUser(null) } }

  return <div className="app-shell"><div className="ambient ambient-blue" /><div className="ambient ambient-violet" /><header className="site-header"><Logo /><nav>{user ? <button className="nav-account" type="button" onClick={logout}>Quitter l’espace</button> : <><button className="nav-login" type="button" onClick={() => setAuthMode('login')}>Se connecter</button><button className="button button-primary button-small" type="button" onClick={() => setAuthMode('register')}>Créer un compte</button></>}</nav></header><main>{user ? <Dashboard user={user} onLogout={logout} /> : <Home openAuth={setAuthMode} />}</main>{authMode && <AuthDialog mode={authMode} setMode={setAuthMode} onSuccess={(nextUser) => { setUser(nextUser); setAuthMode(null) }} onClose={() => setAuthMode(null)} />}</div>
}
