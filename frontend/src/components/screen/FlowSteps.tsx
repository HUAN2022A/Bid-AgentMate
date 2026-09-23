import { STATE_META } from '../../api'

interface FlowStepsProps {
  /** 状态机全步骤（state key 顺序） */
  flow: string[]
  /** 当前 state 下标 */
  current: number
}

/** 异常分支态：不作为正向里程碑点亮（暗红空心；成为当前态时红色呼吸告警）。 */
const ERROR_STATES = new Set(['parse_failed'])

/** 顶栏状态机流程灯：走过的亮青色、当前步呼吸灯、未来步暗置、异常分支红系差异化。 */
export default function FlowSteps({ flow, current }: FlowStepsProps) {
  if (!flow.length) {
    return <div className="sc-flow" style={{ color: '#475569', letterSpacing: 2 }}>状态机待机</div>
  }
  const cur = Math.min(Math.max(current, 0), flow.length - 1)
  return (
    <div className="sc-flow">
      {flow.map((s, i) => {
        const err = ERROR_STATES.has(s)
        return (
          <div
            key={`${s}-${i}`}
            className={`sc-flow-step ${err ? 'err' : ''} ${i < cur ? 'done' : ''} ${i === cur ? 'now' : ''}`}
          >
            <span className="sc-flow-dot" />
            <span className="sc-flow-label">{STATE_META[s]?.label ?? s}</span>
            {i < flow.length - 1 && <span className="sc-flow-line" />}
          </div>
        )
      })}
    </div>
  )
}
