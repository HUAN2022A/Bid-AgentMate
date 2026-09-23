import { useEffect, useRef } from 'react'

interface Star {
  x: number
  y: number
  r: number
  vx: number
  vy: number
  base: number
  phase: number
  speed: number
  hue: number
}

/** 深空粒子星幕：canvas 自绘，80~120 个粒子缓慢漂移 + 闪烁（rAF 驱动，卸载时 cancel）。 */
export default function Starfield() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    let w = 0
    let h = 0
    let stars: Star[] = []
    const DPR = Math.min(2, window.devicePixelRatio || 1)

    const resize = () => {
      w = canvas.clientWidth
      h = canvas.clientHeight
      canvas.width = Math.max(1, Math.round(w * DPR))
      canvas.height = Math.max(1, Math.round(h * DPR))
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0)
      const count = Math.min(120, Math.max(80, Math.round((w * h) / 20000)))
      stars = Array.from({ length: count }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 0.4 + Math.random() * 1.4,
        vx: (Math.random() - 0.5) * 0.07,
        vy: (Math.random() - 0.5) * 0.07,
        base: 0.22 + Math.random() * 0.5,
        phase: Math.random() * Math.PI * 2,
        speed: 0.4 + Math.random() * 1.3,
        // 多数青蓝、少数暖白，贴深空配色
        hue: Math.random() < 0.72 ? 190 : 215,
      }))
    }

    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(canvas)
    resize()

    let last = performance.now()
    const draw = (now: number) => {
      const dt = Math.min(50, now - last)
      last = now
      ctx.clearRect(0, 0, w, h)
      for (const s of stars) {
        s.x += (s.vx * dt) / 16
        s.y += (s.vy * dt) / 16
        s.phase += (s.speed * dt) / 1000
        if (s.x < -3) s.x = w + 3
        else if (s.x > w + 3) s.x = -3
        if (s.y < -3) s.y = h + 3
        else if (s.y > h + 3) s.y = -3
        const twinkle = 0.55 + 0.45 * Math.sin(s.phase)
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
        ctx.fillStyle = `hsla(${s.hue}, 92%, ${68 + s.r * 8}%, ${(s.base * twinkle).toFixed(3)})`
        ctx.fill()
        // 大粒子拖一条微光晕
        if (s.r > 1.3) {
          ctx.beginPath()
          ctx.arc(s.x, s.y, s.r * 2.6, 0, Math.PI * 2)
          ctx.fillStyle = `hsla(${s.hue}, 92%, 70%, ${(s.base * twinkle * 0.12).toFixed(3)})`
          ctx.fill()
        }
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(raf)
      resizeObserver.disconnect()
    }
  }, [])

  return <canvas ref={canvasRef} className="sc-starfield" aria-hidden />
}
