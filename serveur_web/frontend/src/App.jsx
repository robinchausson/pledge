import { useEffect, useState, useRef } from 'react'

const API_URL = import.meta.env.VITE_API_URL || ''

const featured = [
  { title: 'Beyond the ridge', type: 'Documentaire', meta: '2 h 04', className: 'art-mountain' },
  { title: 'Signal / 01', type: 'Série originale', meta: 'Saison 1', className: 'art-signal' },
  { title: 'Night transmissions', type: 'Live session', meta: 'En direct', className: 'art-night' },
]

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
      <span className="logo-mark" aria-hidden="true"><span /></span>
      <span className="logo-copy"><strong>Pledge</strong><small>STREAM BEYOND</small></span>
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
      const data = await apiRequest(isRegister ? '/auth/register' : '/auth/login', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
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

function Home({ openAuth }) {
  return (
    <>
      <section className="hero" id="top">
        <div className="hero-copy">
          <span className="eyebrow">Play + Edge</span>
          <h1>Stream beyond<br /><em>the expected.</em></h1>
          <p>Vos histoires préférées, sans limites. Une nouvelle vision du streaming qui vous emmène toujours plus loin.</p>
          <div className="hero-actions"><button className="button button-primary" onClick={() => openAuth('register')}>Commencer l’expérience <span>→</span></button><button className="button button-quiet" onClick={() => openAuth('login')}>J’ai déjà un compte</button></div>
          <div className="proof-row"><span><b>01</b> Expériences originales</span><span><b>∞</b> Toujours plus loin</span></div>
        </div>
        <div className="hero-stage" aria-label="Aperçu de l'expérience Pledge"><div className="stage-glow" /><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="showcase"><div className="showcase-top"><span>PLEDGE / FEATURED</span><span>•••</span></div><div className="showcase-art"><div className="mountain-shape" /><button className="big-play" type="button" onClick={() => openAuth('register')} aria-label="Commencer à regarder"><PlayIcon /></button><span className="art-label">BEYOND THE RIDGE</span></div><div className="showcase-bottom"><div><strong>Beyond the ridge</strong><small>Une expérience sans frontières</small></div><span className="live-pill"><i /> LIVE</span></div></div></div>
      </section>
      <section className="catalog-section" id="discover"><div className="section-heading"><div><span className="eyebrow">À découvrir</span><h2>Un univers à explorer</h2></div><p>Films · Séries · Expériences live</p></div><div className="catalog-grid">{featured.map((item) => <article className="catalog-card" key={item.title}><div className={`catalog-art ${item.className}`}><span>{item.type}</span><button type="button" onClick={() => openAuth('register')} aria-label={`Regarder ${item.title}`}><PlayIcon /></button></div><div className="catalog-info"><div><h3>{item.title}</h3><small>{item.type}</small></div><span>{item.meta}</span></div></article>)}</div></section>
      <section className="manifesto"><div className="manifesto-mark"><span>P</span></div><div><span className="eyebrow">La promesse Pledge</span><h2>Plus qu’un streaming.<br /><em>Une nouvelle vision.</em></h2></div><p>Play est notre point de départ. Edge est ce qui nous pousse à aller plus loin, à chaque histoire.</p></section>
    </>
  )
}

function StreamerInterface({ user }) {
  const [streaming, setStreaming] = useState(false)
  const [ws, setWs] = useState(null)
  const [mediaRecorder, setMediaRecorder] = useState(null)
  const videoRef = useRef()

  async function startStream() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      videoRef.current.srcObject = stream
      
      const socket = new WebSocket(`ws://localhost:8000/api/stream/publish/${user.email}`)
      socket.onopen = () => {
        setStreaming(true)
        // Note: mimeType fallback if vp8 is not supported on this browser (ex: Safari)
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
        const recorder = new MediaRecorder(stream, { mimeType: mime })
        recorder.ondataavailable = async (e) => {
          if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) {
            socket.send(await e.data.arrayBuffer())
          }
        }
        recorder.start(1000)
        setMediaRecorder(recorder)
      }
      
      setWs(socket)
    } catch (e) {
      console.error('Erreur caméra:', e)
      alert("Erreur d'accès à la caméra")
    }
  }

  function stopStream() {
    if (mediaRecorder) mediaRecorder.stop()
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
        <span className="connection-badge"><i style={{background: streaming ? '#22c55e' : '#ef4444'}} /> {streaming ? 'En ligne' : 'Hors ligne'}</span>
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
  const videoRef = useRef()
  const [ws, setWs] = useState(null)
  
  function startWatching() {
    if (!streamId) return
    setWatching(true)
    
    const socket = new WebSocket(`ws://localhost:8000/api/stream/watch/${streamId}`)
    socket.binaryType = "arraybuffer"
    
    const mediaSource = new MediaSource()
    // Needs a slight delay for react to mount video ref if we just toggled watching
    setTimeout(() => {
      if (videoRef.current) {
        videoRef.current.src = URL.createObjectURL(mediaSource)
      }
    }, 50)
    
    mediaSource.addEventListener('sourceopen', () => {
      const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
      const sourceBuffer = mediaSource.addSourceBuffer(mime)
      const queue = []
      
      socket.onmessage = (event) => {
        const chunk = new Uint8Array(event.data)
        if (!sourceBuffer.updating && queue.length === 0) {
          sourceBuffer.appendBuffer(chunk)
        } else {
          queue.push(chunk)
        }
      }
      
      sourceBuffer.addEventListener('updateend', () => {
        if (queue.length > 0 && !sourceBuffer.updating) {
          sourceBuffer.appendBuffer(queue.shift())
        }
      })
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
      </div>
      {!watching ? (
        <div style={{display: 'flex', gap: '1rem', marginTop: '1rem'}}>
          <input 
            style={{flex: 1, padding: '0.8rem', borderRadius: '8px', border: '1px solid #333', background: '#111', color: '#fff'}}
            placeholder="Email du diffuseur" 
            value={streamId} 
            onChange={e => setStreamId(e.target.value)} 
          />
          <button className="button button-primary" onClick={startWatching}>Rejoindre</button>
        </div>
      ) : (
        <>
          <video ref={videoRef} autoPlay controls style={{width: '100%', background: '#000', borderRadius: '12px', minHeight: '200px', marginTop: '1rem'}} />
          <div style={{marginTop: '1rem'}}>
            <button className="button button-quiet" onClick={stopWatching}>Arrêter de regarder</button>
          </div>
        </>
      )}
    </article>
  )
}

function Dashboard({ user, onLogout }) {
  return (
    <section className="dashboard">
      <div className="dashboard-intro">
        <div><span className="eyebrow">Espace personnel</span><h1>Bienvenue,<br /><em>{user.email?.split('@')[0]}.</em></h1></div>
        <button className="button button-quiet" type="button" onClick={onLogout}>Se déconnecter</button>
      </div>
      <div className="dashboard-grid">
        {user.role === 'streamer' ? <StreamerInterface user={user} /> : <ViewerInterface user={user} />}
        <aside className="dashboard-card profile-card">
          <span className="avatar">{user.email?.charAt(0).toUpperCase()}</span>
          <span className="eyebrow">Votre profil</span>
          <h2>{user.email}</h2>
          <dl>
            <div><dt>Rôle</dt><dd>{user.role || 'viewer'}</dd></div>
            <div><dt>Statut</dt><dd className="green">Actif</dd></div>
          </dl>
        </aside>
      </div>
    </section>
  )
}

export default function App() {
  const [authMode, setAuthMode] = useState(null)
  const [user, setUser] = useState(null)

  async function logout() { try { await apiRequest('/auth/logout', { method: 'POST' }) } finally { setUser(null) } }

  return <div className="app-shell"><div className="ambient ambient-blue" /><div className="ambient ambient-violet" /><header className="site-header"><Logo /><nav><a href="#discover">Découvrir</a>{user ? <button className="nav-account" type="button" onClick={logout}>Quitter l’espace</button> : <><button className="nav-login" type="button" onClick={() => setAuthMode('login')}>Se connecter</button><button className="button button-primary button-small" type="button" onClick={() => setAuthMode('register')}>Créer un compte</button></>}</nav></header><main>{user ? <Dashboard user={user} onLogout={logout} /> : <Home openAuth={setAuthMode} />}</main>{authMode && <AuthDialog mode={authMode} setMode={setAuthMode} onSuccess={(nextUser) => { setUser(nextUser); setAuthMode(null) }} onClose={() => setAuthMode(null)} />}</div>
}
