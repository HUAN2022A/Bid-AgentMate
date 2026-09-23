import type { CSSProperties, ReactNode } from 'react'

interface PanelProps {
  title?: string
  sub?: string
  className?: string
  /** 入场动画延迟（ms），面板间交错 */
  delay?: number
  /** 顶部扫光延迟（s），面板间错开 */
  sweep?: number
  style?: CSSProperties
  children: ReactNode
}

function Corner({ pos }: { pos: 'tl' | 'tr' | 'br' | 'bl' }) {
  return (
    <svg className={`sc-corner ${pos}`} viewBox="0 0 14 14" aria-hidden>
      <path d="M1 13 V1 H13" />
    </svg>
  )
}

/** 大屏面板：半透明深底 + blur + 青色描边 + 外发光 + SVG 四角描角 + 顶部扫光 + 入场动画。 */
export default function Panel({ title, sub, className = '', delay = 0, sweep = 0, style, children }: PanelProps) {
  const vars = { '--sweep-delay': `${sweep}s` } as CSSProperties
  return (
    <section
      className={`sc-panel sc-enter ${className}`}
      style={{ ...vars, ...style, animationDelay: `${delay}ms` }}
    >
      <Corner pos="tl" />
      <Corner pos="tr" />
      <Corner pos="br" />
      <Corner pos="bl" />
      {title && (
        <header className="sc-panel-title">
          <i className="sc-title-bar" />
          {title}
          {sub && <span className="sc-panel-sub sc-num">{sub}</span>}
        </header>
      )}
      <div className="sc-panel-body">{children}</div>
    </section>
  )
}
