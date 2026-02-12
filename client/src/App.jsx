import { useState, useEffect, useRef } from 'react'
import movie1 from '../assets/movie1.webp'
import movie2 from '../assets/movie2.webp'
import movie3 from '../assets/movie3.webp'
import movie4 from '../assets/movie4.webp'
import './App.css'

const MOVIES = [movie1, movie2, movie3, movie4]

// Faster decision = stronger score. Max 100 points at instant, decays over ~4 seconds.
function pointsFromTime(elapsedMs, kept) {
  const elapsedSec = elapsedMs / 1000
  const raw = Math.round(100 - elapsedSec * 25)
  const points = Math.max(0, Math.min(100, raw))
  return kept ? points : -points
}

function App() {
  const [currentIndex, setCurrentIndex] = useState(0)
  const [lastRoundScore, setLastRoundScore] = useState(null)
  const [startTime, setStartTime] = useState(() => Date.now())
  const [gameOver, setGameOver] = useState(false)
  const isFirstRender = useRef(true)

  const currentMovie = MOVIES[currentIndex]
  const hasMore = currentIndex < MOVIES.length

  // Reset timer and drag when we show a new card (skip on first mount so we don't double-trigger)
  useEffect(() => {
    setDragOffset({ x: 0, y: 0 })
    if (isFirstRender.current) {
      isFirstRender.current = false
      return
    }
    setStartTime(Date.now())
  }, [currentIndex])

  function handleChoice(kept) {
    const elapsed = Date.now() - startTime
    const points = pointsFromTime(elapsed, kept)
    setLastRoundScore(points)

    if (currentIndex + 1 >= MOVIES.length) {
      setGameOver(true)
    } else {
      setCurrentIndex((i) => i + 1)
    }
  }

  function handleRestart() {
    setCurrentIndex(0)
    setLastRoundScore(null)
    setGameOver(false)
    setStartTime(Date.now())
  }

  // Drag-to-swipe state
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 })
  const dragStartRef = useRef(null)

  const DRAG_THRESHOLD = 80

  const dragOffsetRef = useRef(dragOffset)
  dragOffsetRef.current = dragOffset

  function onPointerDown(e) {
    e.preventDefault()
    dragStartRef.current = { x: e.clientX, y: e.clientY }
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
    document.addEventListener('pointercancel', onPointerUp)
  }

  function onPointerMove(e) {
    if (!dragStartRef.current) return
    const dx = e.clientX - dragStartRef.current.x
    const dy = e.clientY - dragStartRef.current.y
    setDragOffset({ x: dx, y: dy })
  }

  function onPointerUp() {
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', onPointerUp)
    document.removeEventListener('pointercancel', onPointerUp)
    const { x } = dragOffsetRef.current
    if (x >= DRAG_THRESHOLD) {
      handleChoice(true)
    } else if (x <= -DRAG_THRESHOLD) {
      handleChoice(false)
    } else {
      setDragOffset({ x: 0, y: 0 })
    }
    dragStartRef.current = null
  }

  if (gameOver) {
    const displayScore = lastRoundScore != null
      ? (lastRoundScore >= 0 ? `+${lastRoundScore}` : String(lastRoundScore))
      : '—'
    return (
      <div className="app">
        <h1>Movie Tinder</h1>
        <div className="results-card">
          <p className="final-score">Previous score</p>
          <p className="score-value">{displayScore}</p>
          <button type="button" className="restart-btn" onClick={handleRestart}>
            Play again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <h1>Movie Tinder</h1>
      <p className="instruction">Keep or toss — the faster you decide, the bigger the score swing</p>

      <div className="score-bar">
        <span className="previous-score">
          Previous: {lastRoundScore != null ? (lastRoundScore >= 0 ? `+${lastRoundScore}` : String(lastRoundScore)) : '—'}
        </span>
        <span className="card-count">{currentIndex + 1} / {MOVIES.length}</span>
      </div>

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
