import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import {
  BulbOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  TrophyOutlined,
  WarningOutlined,
  CheckCircleFilled,
} from '@ant-design/icons'
import type { EChartsCoreOption } from 'echarts/core'
import { getScreenData, STATE_META, CHAPTER_STATE_META } from '../api'
import type { ScreenDataOut } from '../api'
import Panel from '../components/screen/Panel'
import Starfield from '../components/screen/Starfield'
import FlowSteps from '../components/screen/FlowSteps'
import CopilotFeed from '../components/screen/CopilotFeed'
import CoverageHeatmap from '../components/screen/CoverageHeatmap'
import { useCountUp } from '../components/screen/useCountUp'
import { useEChart } from '../components/screen/echartsSetup'
import '../components/screen/screen.css'

/* ---------- 稳健性工具：任何字段缺失都回退 0 / —，绝不 NaN ---------- */

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}
function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}
function text(v: unknown): string {
  return typeof v === 'string' && v ? v : '—'
}

const PALETTE = ['#22d3ee', '#3b82f6', '#818cf8', '#34d399', '#fbbf24', '#f472b6', '#f97316', '#a78bfa']

/* ---------- 实时时钟 ---------- */

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])
  const p = (n: number) => String(n).padStart(2, '0')
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()] ?? ''
  return (
    <div className="sc-clock">
      <div className="sc-clock-time sc-num">
        {p(now.getHours())}:{p(now.getMinutes())}:{p(now.getSeconds())}
      </div>
      <div className="sc-clock-date sc-num">
        {now.getFullYear()}-{p(now.getMonth() + 1)}-{p(now.getDate())} 周{week}
      </div>
    </div>
  )
}

/* ---------- 中央 count-up 大数字 ---------- */

function BigPercent({ value, size }: { value: number; size?: 'lg' | 'md' }) {
  const animated = useCountUp(value, 900)
  const shown = Math.round(animated)
  if (size === 'md') {
    return (
      <span className="sc-collab-num sc-num">
        {shown}
        <span className="sc-collab-unit">%</span>
      </span>
    )
  }
  return (
    <span className="sc-gauge-pct sc-num">
      {shown}
      <small>%</small>
    </span>
  )
}

/* ---------- 页面 ---------- */

export default function ScreenPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id) || 0

  const { data, isError } = useQuery({
    queryKey: ['screen', pid],
    queryFn: () => getScreenData(pid),
    refetchInterval: 5000,
    retry: 1,
  })

  /* 1920×1080 设计画布按视口等比缩放居中 */
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const update = () =>
      setScale(Math.min(window.innerWidth / 1920, window.innerHeight / 1080))
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  /* ---- 全部派生数据都做缺失回退（接口未就绪时整屏 0 值占位不破版） ---- */
  const d: ScreenDataOut | undefined = data
  const summary = d?.summary
  const totalWords = num(summary?.total_words)
  const targetWords = num(summary?.target_words)
  const progressPct = targetWords > 0 ? Math.min(100, (totalWords / targetWords) * 100) : 0
  const copilot = d?.copilot
  const applyRate = clamp01(num(copilot?.apply_rate))
  const coverage = d?.coverage
  const cov = num(coverage?.summary?.covered)
  const covPart = num(coverage?.summary?.partial)
  const covNone = num(coverage?.summary?.none)
  const coverageRate = cov + covPart + covNone > 0 ? cov / (cov + covPart + covNone) : 0
  const starReqs = num(summary?.star_reqs)
  const starHit = num(summary?.star_hit)
  const starRate = starReqs > 0 ? clamp01(starHit / starReqs) : 0
  const materials = d?.materials
  const matTotal =
    num(materials?.case) + num(materials?.person) + num(materials?.credential) + num(materials?.ip) + num(materials?.capability)
  const matReady = clamp01(matTotal / 20)
  const scoreDist = useMemo(
    () => (Array.isArray(d?.score_dist) ? d!.score_dist.filter(Boolean) : []),
    [d],
  )
  const chapters = useMemo(
    () => (Array.isArray(d?.chapters) ? d!.chapters.filter(Boolean) : []),
    [d],
  )
  const risks = useMemo(
    () => (Array.isArray(d?.risks) ? d!.risks.filter((r) => typeof r === 'string' && r) : []),
    [d],
  )
  const flow = useMemo(() => (Array.isArray(d?.flow) ? d!.flow.filter(Boolean) : []), [d])
  const currentStep = num(d?.current_step_index)

  /* ---- 环形仪表（总进度） ---- */
  const gaugeOption = useMemo<EChartsCoreOption>(
    () => ({
      animationDurationUpdate: 800,
      animationEasingUpdate: 'cubicOut',
      series: [
        {
          id: 'progress',
          type: 'gauge',
          startAngle: 220,
          endAngle: -40,
          min: 0,
          max: 100,
          radius: '94%',
          center: ['50%', '56%'],
          progress: {
            show: true,
            width: 16,
            roundCap: true,
            itemStyle: {
              color: {
                type: 'linear',
                x: 0, y: 0, x2: 1, y2: 0,
                colorStops: [
                  { offset: 0, color: '#22d3ee' },
                  { offset: 1, color: '#3b82f6' },
                ],
              },
              shadowColor: 'rgba(34, 211, 238, 0.8)',
              shadowBlur: 18,
            },
          },
          axisLine: { roundCap: true, lineStyle: { width: 16, color: [[1, 'rgba(30, 48, 80, 0.7)']] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: false },
          anchor: { show: false },
          detail: { show: false },
          title: { show: false },
          data: [{ value: Number(progressPct.toFixed(1)) }],
        },
        {
          id: 'progress-echo',
          type: 'gauge',
          startAngle: 220,
          endAngle: -40,
          min: 0,
          max: 100,
          radius: '76%',
          center: ['50%', '56%'],
          progress: {
            show: true,
            width: 2,
            roundCap: true,
            itemStyle: { color: 'rgba(103, 232, 249, 0.45)', shadowColor: 'rgba(34,211,238,0.6)', shadowBlur: 8 },
          },
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: false },
          anchor: { show: false },
          detail: { show: false },
          title: { show: false },
          data: [{ value: Number(progressPct.toFixed(1)) }],
        },
      ],
    }),
    [progressPct],
  )

  /* ---- 评分分布（发光玫瑰图） ---- */
  const pieOption = useMemo<EChartsCoreOption>(
    () => ({
      animationDurationUpdate: 600,
      color: PALETTE,
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(5, 11, 24, 0.92)',
        borderColor: 'rgba(34, 211, 238, 0.4)',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: '{b}<br/>分值合计 {c} 分 · {d}%',
      },
      series: [
        {
          id: 'score-rose',
          type: 'pie',
          roseType: 'radius',
          radius: ['32%', '68%'],
          center: ['50%', '54%'],
          itemStyle: {
            borderColor: '#050b18',
            borderWidth: 2,
            shadowColor: 'rgba(34, 211, 238, 0.4)',
            shadowBlur: 14,
          },
          label: { color: '#a5f3fc', fontSize: 11, formatter: '{b}\n{c}分' },
          labelLine: { length: 6, length2: 8, lineStyle: { color: 'rgba(148, 163, 184, 0.5)' } },
          data: scoreDist.map((s) => ({
            name: s?.category || '未分类',
            value: Number(num(s?.score).toFixed(1)),
          })),
        },
      ],
    }),
    [scoreDist],
  )

  /* ---- 五维健康雷达 ---- */
  const radarOption = useMemo<EChartsCoreOption>(
    () => ({
      animationDurationUpdate: 600,
      radar: {
        center: ['50%', '54%'],
        radius: '58%',
        indicator: [
          { name: '进度', max: 1 },
          { name: '覆盖率', max: 1 },
          { name: '星级命中', max: 1 },
          { name: 'AI 协作', max: 1 },
          { name: '素材就绪', max: 1 },
        ],
        axisName: { color: '#c3d3e8', fontSize: 13 },
        axisNameGap: 12,
        splitNumber: 4,
        axisLine: { lineStyle: { color: 'rgba(34, 211, 238, 0.22)' } },
        splitLine: { lineStyle: { color: 'rgba(34, 211, 238, 0.16)' } },
        splitArea: { areaStyle: { color: ['rgba(34, 211, 238, 0.03)', 'rgba(34, 211, 238, 0.07)'] } },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              name: '健康度',
              value: [
                Number(clamp01(progressPct / 100).toFixed(3)),
                Number(coverageRate.toFixed(3)),
                Number(starRate.toFixed(3)),
                Number(applyRate.toFixed(3)),
                Number(matReady.toFixed(3)),
              ],
              symbol: 'circle',
              symbolSize: 5,
              lineStyle: { color: '#22d3ee', width: 2, shadowColor: 'rgba(34, 211, 238, 0.85)', shadowBlur: 12 },
              itemStyle: { color: '#67e8f9' },
              areaStyle: { color: 'rgba(34, 211, 238, 0.22)' },
            },
          ],
        },
      ],
    }),
    [progressPct, coverageRate, starRate, applyRate, matReady],
  )

  const gaugeRef = useEChart(gaugeOption)
  const pieRef = useEChart(pieOption)
  const radarRef = useEChart(radarOption)

  const pieTotal = scoreDist.reduce((acc, s) => acc + num(s?.score), 0)
  const doneChapters = num(summary?.done_chapters)
  const totalChapters = num(summary?.total_chapters)
  const pendingGaps = num(summary?.pending_gaps)
  const priceHits = num(summary?.price_hits)

  const MAT_ITEMS = [
    { key: 'case', label: '案例业绩', icon: <TrophyOutlined /> },
    { key: 'person', label: '人员配置', icon: <TeamOutlined /> },
    { key: 'credential', label: '资信证明', icon: <SafetyCertificateOutlined /> },
    { key: 'ip', label: '知识产权', icon: <BulbOutlined /> },
    { key: 'capability', label: '能力资质', icon: <RocketOutlined /> },
  ] as const

  const canvasStyle: CSSProperties = { transform: `scale(${scale})` }

  return (
    <div className="sc-root">
      <Starfield />
      <div className="sc-grid-overlay" />
      <div className="sc-viewport">
        <div className="sc-canvas" style={canvasStyle}>
          {/* ============ 顶栏 ============ */}
          <Panel className="sc-topbar" delay={0} sweep={0}>
            <div className="sc-top-left">
              <div className="sc-proj-name" title={text(d?.project?.name)}>
                {text(d?.project?.name)}
              </div>
              <div className="sc-proj-meta">
                <span className="sc-num">NO.{text(d?.project?.tender_no)}</span>
                <span className="sep">|</span>
                <span className="sc-state-chip">
                  {STATE_META[d?.project?.state ?? '']?.label ?? text(d?.project?.state)}
                </span>
              </div>
            </div>
            <FlowSteps flow={flow} current={currentStep} />
            <Clock />
          </Panel>

          {/* ============ 三列主区 ============ */}
          <div className="sc-mid">
            {/* 左列：评分分布 + 覆盖热力图 */}
            <div className="sc-col left">
              <Panel title="评分分布" sub={`${scoreDist.length} 类`} className="fix-h" delay={90} sweep={1.2}>
                <div className="sc-chart" ref={pieRef} />
                {pieTotal <= 0 && <div className="sc-chart-hint">暂无评分点数据</div>}
              </Panel>
              <Panel
                title="评分覆盖矩阵"
                sub={`${cov}/${cov + covPart + covNone} 命中`}
                className="sc-panel"
                delay={180}
                sweep={3.1}
                style={{ flex: 1 }}
              >
                <CoverageHeatmap coverage={coverage} />
              </Panel>
            </div>

            {/* 中央：总进度仪表 + 人机协作 */}
            <div className="sc-col center">
              <Panel title="标书编写作战核心" delay={270} sweep={2.0} style={{ flex: 1 }}>
                <div className="sc-center-wrap">
                  <div className="sc-gauge-box">
                    <div className="sc-chart" ref={gaugeRef} />
                    <div className="sc-gauge-overlay">
                      <BigPercent value={progressPct} />
                      <div className="sc-gauge-label">总进度</div>
                      <div className="sc-gauge-words sc-num">
                        {totalWords.toLocaleString()} / {targetWords.toLocaleString()} 字
                      </div>
                    </div>
                  </div>
                  <div className="sc-collab">
                    <div>
                      <div className="sc-collab-rate">
                        <BigPercent value={applyRate * 100} size="md" />
                      </div>
                      <div className="sc-collab-cap">人机协作率</div>
                      <div className="sc-collab-cap" style={{ color: '#94a3b8' }}>
                        {num(copilot?.total)} 次建议 · {num(copilot?.applied)} 次采纳
                      </div>
                    </div>
                    <div className="sc-kv">
                      <div className="sc-kv-row">
                        <span className="k">章节完成</span>
                        <span className="v good sc-num">
                          {doneChapters} / {totalChapters}
                        </span>
                      </div>
                      <div className="sc-kv-row">
                        <span className="k">待补材料</span>
                        <span className={`v sc-num ${pendingGaps > 0 ? 'warn' : 'good'}`}>{pendingGaps}</span>
                      </div>
                      <div className="sc-kv-row">
                        <span className="k">价格命中</span>
                        <span className="v sc-num">{priceHits}</span>
                      </div>
                      <div className="sc-kv-row">
                        <span className="k">星级条款命中</span>
                        <span className="v sc-num">
                          {starHit} / {starReqs}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </Panel>
            </div>

            {/* 右列：健康雷达 + AI 协作动态 */}
            <div className="sc-col right">
              <Panel title="五维健康雷达" className="fix-h" delay={360} sweep={0.6}>
                <div className="sc-chart" ref={radarRef} />
              </Panel>
              <Panel title="AI 协作动态" delay={450} sweep={4.0} style={{ flex: 1 }}>
                <CopilotFeed recent={copilot?.recent ?? []} />
              </Panel>
            </div>
          </div>

          {/* ============ 底部横条 ============ */}
          <div className="sc-bottom">
            <Panel title="章节进度" sub={`${chapters.length} 章`} className="grow" delay={540} sweep={1.6}>
              <div className="sc-chapters">
                {chapters.length === 0 && <div className="sc-chap-empty">暂无章节数据</div>}
                {chapters.map((c) => {
                  const words = num(c?.words)
                  const target = num(c?.target)
                  const pct = target > 0 ? Math.min(100, Math.round((words / target) * 100)) : 0
                  const done = c?.state === 'draft_done' || c?.state === 'edited'
                  const stateLabel = CHAPTER_STATE_META[c?.state ?? '']?.label ?? c?.state ?? ''
                  return (
                    <div
                      className="sc-chap"
                      key={c?.key ?? c?.title ?? 'chap'}
                      title={`${c?.key ?? ''} ${c?.title ?? ''}（${stateLabel}）`}
                    >
                      <span className="sc-chap-key sc-num">{c?.key}</span>
                      <span className="sc-chap-title">{c?.title || '—'}</span>
                      <div className="sc-chap-bar">
                        <i className={pct >= 100 ? 'full' : ''} style={{ width: `${pct}%` }} />
                      </div>
                      <span className="sc-chap-pct sc-num">{pct}%</span>
                      {done && <span className="sc-chap-check">✓</span>}
                    </div>
                  )
                })}
              </div>
            </Panel>

            <Panel title="素材弹药库" sub={`共 ${matTotal} 件`} className="mat" delay={630} sweep={2.6}>
              <div className="sc-mats">
                {MAT_ITEMS.map((m) => (
                  <div className="sc-mat" key={m.key}>
                    <span className="sc-mat-icon">{m.icon}</span>
                    <span className="sc-mat-count sc-num">{num(materials?.[m.key])}</span>
                    <span className="sc-mat-label">{m.label}</span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="风险清单" sub={`${risks.length} 项`} className="risk" delay={720} sweep={3.6}>
              <div className="sc-risk-list">
                {risks.length === 0 ? (
                  <div className="sc-risk-none">
                    <CheckCircleFilled /> 暂无风险
                  </div>
                ) : (
                  risks.slice(0, 6).map((r, i) => (
                    <div className="sc-risk-item" key={i} title={r}>
                      <WarningOutlined className="sc-risk-icon" />
                      <span className="sc-risk-text">{r}</span>
                    </div>
                  ))
                )}
              </div>
            </Panel>
          </div>

          {/* 数据源状态角标（接口未就绪时提示，不遮内容） */}
          <div className="sc-boot">
            {isError ? (
              <>接口待接入 · 5s 自动重试</>
            ) : data ? (
              <>
                <span className="dot">●</span> 实时同步
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
