import { useEffect, useRef, useState } from 'react'

/** 数字滚动：值变化时从当前显示值缓动到目标值（ease-out cubic，默认 800ms）。 */
export function useCountUp(target: number, duration = 800): number {
  const [value, setValue] = useState(0)
  const fromRef = useRef(0)

  useEffect(() => {
    const from = fromRef.current
    if (!Number.isFinite(target)) return
    if (from === target) {
      setValue(target)
      return
    }
    const start = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      const v = from + (target - from) * eased
      fromRef.current = v
      setValue(v)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])

  return Number.isFinite(value) ? value : 0
}
