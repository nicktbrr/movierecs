import { useState, useEffect, useRef } from 'react'
import movie1 from '../assets/movie1.webp'
import movie2 from '../assets/movie2.webp'
import movie3 from '../assets/movie3.webp'
import movie4 from '../assets/movie4.webp'
import './App.css'

const MOVIES = [movie1, movie2, movie3, movie4]
const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

function App() {
  const [gameStarted, setGameStarted] = useState(false)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [gameOver, setGameOver] = useState(false)
  const [driftScores, setDriftScores] = useState(null) // { drift_scores: number[], overall_drift: number }

  const currentMovie = MOVIES[currentIndex]

  // When card is shown, record time (for Initial Latency: time-to-first-movement)
  const cardShownAtRef = useRef(Date.now())
  useEffect(() => {
    setDragOffset({ x: 0, y: 0 })
    cardShownAtRef.current = Date.now()
  }, [currentIndex])

  // Accumulated metrics per card (sent to backend when all 4 done)
  const metricsPerCardRef = useRef([])

  function handleChoice(kept) {
    const isLastMovie = currentIndex + 1 >= MOVIES.length
    if (isLastMovie) {
      const payload = { metrics: metricsPerCardRef.current }
      fetch(`${API_BASE}/session-metrics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then((res) => res.json())
        .then((data) => {
          setDriftScores({
            drift_scores: data.drift_scores ?? [],
            overall_drift: data.overall_drift ?? 0,
            directions: metricsPerCardRef.current.map((m) => m.direction),
          })
          setGameOver(true)
        })
        .catch(() => setGameOver(true))
    } else {
      setCurrentIndex((i) => i + 1)
    }
  }

  function handleRestart() {
    setCurrentIndex(0)
    setGameOver(false)
    setDriftScores(null)
    metricsPerCardRef.current = []
  }

  // ---- Drag state ----
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 })
  const dragStartRef = useRef(null)
  const pointerDownAtRef = useRef(null)
  const moveHistoryRef = useRef([])
  const firstMoveAtRef = useRef(null)
  const prevDxRef = useRef(null)
  const xFlipsRef = useRef(0)

  const DRAG_THRESHOLD = 80
  const dragOffsetRef = useRef(dragOffset)
  dragOffsetRef.current = dragOffset

  function onPointerDown(e) {
    e.preventDefault()
    dragStartRef.current = { x: e.clientX, y: e.clientY }
    pointerDownAtRef.current = Date.now()
    moveHistoryRef.current = []
    firstMoveAtRef.current = null
    prevDxRef.current = null
    xFlipsRef.current = 0
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
    document.addEventListener('pointercancel', onPointerUp)
  }

  function onPointerMove(e) {
    if (!dragStartRef.current) return
    const dx = e.clientX - dragStartRef.current.x
    const dy = e.clientY - dragStartRef.current.y
    const t = Date.now()

    if (firstMoveAtRef.current == null) firstMoveAtRef.current = t
    moveHistoryRef.current.push({ x: dx, y: dy, t })

    if (prevDxRef.current !== null && dx !== 0) {
      const prevSign = Math.sign(prevDxRef.current)
      const sign = Math.sign(dx)
      if (prevSign !== 0 && sign !== 0 && prevSign !== sign) xFlipsRef.current += 1
    }
    prevDxRef.current = dx

    setDragOffset({ x: dx, y: dy })
  }

  function computePeakVelocity(history) {
    if (history.length < 2) return 0
    let maxSpeed = 0
    for (let i = 1; i < history.length; i++) {
      const a = history[i - 1]
      const b = history[i]
      const dt = (b.t - a.t) / 1000
      if (dt <= 0) continue
      const dist = Math.hypot(b.x - a.x, b.y - a.y)
      const speed = dist / dt
      if (speed > maxSpeed) maxSpeed = speed
    }
    return Math.round(maxSpeed * 10) / 10
  }

  function onPointerUp() {
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', onPointerUp)
    document.removeEventListener('pointercancel', onPointerUp)

    const upAt = Date.now()
    const { x, y } = dragOffsetRef.current

    const decisionTimeMs = pointerDownAtRef.current
      ? upAt - pointerDownAtRef.current
      : 0
    const totalDistance = Math.hypot(x, y)
    const decisionTimeSec = decisionTimeMs / 1000
    const velocityPxPerSec = decisionTimeSec > 0 ? totalDistance / decisionTimeSec : 0
    const initialLatencyMs =
      firstMoveAtRef.current != null && cardShownAtRef.current != null
        ? firstMoveAtRef.current - cardShownAtRef.current
        : null
    const xFlips = xFlipsRef.current
    const peakVelocity = computePeakVelocity(moveHistoryRef.current)
    const direction = x >= DRAG_THRESHOLD ? 1 : 0 // 1 = right (good/keep), 0 = left (bad/toss)

    metricsPerCardRef.current.push({
      decision_time_ms: decisionTimeMs,
      velocity_px_per_sec: Math.round(velocityPxPerSec * 10) / 10,
      initial_latency_ms: initialLatencyMs,
      x_flips: xFlips,
      peak_velocity_px_per_sec: peakVelocity,
      direction,
    })

    if (x >= DRAG_THRESHOLD) {
      handleChoice(true)
    } else if (x <= -DRAG_THRESHOLD) {
      handleChoice(false)
    } else {
      metricsPerCardRef.current.pop()
      setDragOffset({ x: 0, y: 0 })
    }
    dragStartRef.current = null
  }

  const appHeader = (
    <header className="app-header">
      <h1 className="app-title">Cinamatch</h1>
      <div className="header-arrows">
        <span className="arrow-label arrow-toss">
          <span className="arrow-symbol">←</span> Toss
        </span>
        <span className="arrow-label arrow-keep">
          Keep <span className="arrow-symbol">→</span>
        </span>
      </div>
    </header>
  )

  // ---- Start popup (before game started) ----
  if (!gameStarted) {
    return (
      <div className="app">
        {appHeader}
        <div className="start-popup">
          <p className="start-instruction">Swipe right to keep, left to toss.</p>
          <button
            type="button"
            className="start-btn"
            onClick={() => setGameStarted(true)}
          >
            Start
          </button>
        </div>
      </div>
    )
  }

  // ---- Game over ----
  if (gameOver) {
    return (
      <div className="app">
        {appHeader}
        <div className="results-card">
          <p className="results-message">All done!</p>
          {driftScores && (
            <div className="drift-scores">
              <p className="drift-label">Kinematic drift (higher = more indecision)</p>
              <p className="drift-overall">
                Overall: <strong>{driftScores.overall_drift}</strong>
              </p>
              <ul className="drift-per-card">
                {driftScores.drift_scores.map((score, i) => (
                  <li key={i}>
                    Image {i + 1}: <strong>{score}</strong> drift
                    {driftScores.directions?.[i] != null && (
                      <> — <strong>{driftScores.directions[i]}</strong> ({driftScores.directions[i] === 1 ? 'kept' : 'tossed'})</>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <button type="button" className="restart-btn" onClick={handleRestart}>
            Play again
          </button>
        </div>
      </div>
    )
  }

  // ---- Main game ----
  return (
    <div className="app">
      {appHeader}
      <p className="instruction">Keep or toss — swipe the card</p>
      <p className="card-count">{currentIndex + 1} / {MOVIES.length}</p>

      <div className="tinder-card-wrap">
        <div
          className="tinder-card tinder-card-draggable"
          style={{
            transform: `translate(${dragOffset.x}px, ${dragOffset.y}px) rotate(${dragOffset.x * 0.03}deg)`,
          }}
          onPointerDown={onPointerDown}
        >
          <img src={currentMovie} alt={`Movie ${currentIndex + 1}`} className="tinder-poster" draggable={false} />
        </div>
      </div>
    </div>
  )
}

export default App
