import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import * as echarts from 'echarts/core'
import { GaugeChart, PieChart, RadarChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'

/** 按需注册：仅大屏用到的图表与渲染器（不整包 import） */
echarts.use([GaugeChart, PieChart, RadarChart, CanvasRenderer])

/**
 * echarts 实例生命周期 hook：init 一次，option 变化时 setOption（默认 merge 模式，
 * 数据刷新走过渡动画而非重绘，避免闪白），卸载时 dispose。
 */
export function useEChart(option: EChartsCoreOption | null): RefObject<HTMLDivElement | null> {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const chart = echarts.init(host, undefined, {
      // scale 放大画布时保持清晰（devicePixelRatio 至少 2）
      devicePixelRatio: Math.max(window.devicePixelRatio || 1, 2),
    })
    chartRef.current = chart
    return () => {
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    if (option && chartRef.current) chartRef.current.setOption(option)
  }, [option])

  return hostRef
}
