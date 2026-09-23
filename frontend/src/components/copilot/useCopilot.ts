/** 章节内 Copilot 状态机：idle → loading →（首个 delta）streaming →（done）preview → (apply | discard) → idle。
 *
 * 契约：AI 只提议，人处置——生成结果先进预览浮层，"应用"才写入编辑器。
 * 应用 = 选区替换（star_response 为光标处插入），事务带 copilotApply meta 供页面判定版本来源，
 * 并向后端打 applied（应用率 = 北极星指标）。
 * 流式为主路径；旧后端无 /copilot/stream（404）自动降级非流式，行为与现状一致。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Editor } from '@tiptap/core'
import { getHTMLFromFragment } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import {
  ApiError,
  markCopilotApplied,
  runCopilot,
  streamCopilot,
  type CopilotActionName,
  type CopilotRequest,
  type CopilotResponse,
} from '../../api'
import { htmlToMd, mdToHtml } from '../../lib/markdown'

const NEIGHBOR_LIMIT = 800

export interface CapturedSelection {
  from: number
  to: number
  md: string
  prevMd: string
  nextMd: string
  /** 捕获时的文档快照：应用前校验文档未变，防止选区错位 */
  doc: PMNode
}

export interface CopilotJob {
  action: CopilotActionName
  instruction: string
  scoringKey: string
  requirementKey: string
  selection: CapturedSelection
}

export type CopilotPhase = 'idle' | 'loading' | 'streaming' | 'preview'

/** 相邻块纯文本（衔接参考）：从选区所在块逐层向外找前/后兄弟块 */
function neighborText(doc: PMNode, pos: number, dir: 'before' | 'after'): string {
  const $pos = doc.resolve(pos)
  for (let d = $pos.depth; d >= 1; d--) {
    const parent = $pos.node(d - 1)
    const idx = dir === 'before' ? $pos.index(d - 1) - 1 : $pos.indexAfter(d - 1)
    if (idx < 0 || idx >= parent.childCount) continue
    const sib = parent.child(idx)
    const text = sib.textBetween(0, sib.content.size, '\n', ' ').trim()
    if (!text) continue
    return dir === 'before' ? text.slice(-NEIGHBOR_LIMIT) : text.slice(0, NEIGHBOR_LIMIT)
  }
  return ''
}

export function captureSelection(editor: Editor): CapturedSelection {
  const { from, to } = editor.state.selection
  const doc = editor.state.doc
  const html = getHTMLFromFragment(doc.slice(from, to).content, editor.schema)
  return {
    from,
    to,
    doc,
    md: htmlToMd(html).trim(),
    prevMd: neighborText(doc, from, 'before'),
    nextMd: neighborText(doc, to, 'after'),
  }
}

interface StartOptions {
  instruction?: string
  scoringKey?: string
  requirementKey?: string
}

export function useCopilot(pid: number, cid: number, editor: Editor | null) {
  const [phase, setPhase] = useState<CopilotPhase>('idle')
  const [job, setJob] = useState<CopilotJob | null>(null)
  const [result, setResult] = useState<CopilotResponse | null>(null)
  /** streaming 阶段逐 delta 累积的打字机文本；done 后渲染以 result.content_md 为准 */
  const [streamText, setStreamText] = useState('')
  const [error, setError] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (phase !== 'loading' && phase !== 'streaming') return
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [phase])

  const fire = useCallback(
    async (next: CopilotJob) => {
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      setJob(next)
      setResult(null)
      setStreamText('')
      setError('')
      setElapsed(0)
      setPhase('loading')
      const body: CopilotRequest = {
        action: next.action,
        instruction: next.instruction,
        scoring_key: next.scoringKey,
        requirement_key: next.requirementKey,
        selection_md: next.action === 'star_response' ? '' : next.selection.md,
        prev_md: next.selection.prevMd,
        next_md: next.selection.nextMd,
      }
      try {
        let res: CopilotResponse
        try {
          res = await streamCopilot(pid, cid, body, {
            onDelta: (t) => {
              setStreamText((s) => s + t)
              setPhase((p) => (p === 'loading' ? 'streaming' : p)) // 首个 delta 即切换
            },
          }, ac.signal)
        } catch (e) {
          // 旧后端无流式端点（404）：自动降级非流式，phase 维持 loading → preview 老路径
          if (!(e instanceof ApiError && e.status === 404)) throw e
          if (ac.signal.aborted) return
          res = await runCopilot(pid, cid, body, ac.signal)
        }
        if (ac.signal.aborted) return
        setResult(res)
        setPhase('preview')
      } catch (e) {
        if (ac.signal.aborted) return
        setError(e instanceof Error ? e.message : '生成失败')
        setPhase('idle')
      }
    },
    [pid, cid],
  )

  const start = useCallback(
    (action: CopilotActionName, opts: StartOptions = {}) => {
      if (!editor) return
      const selection = captureSelection(editor)
      if (action !== 'star_response' && !selection.md) {
        setError('请先选中要处理的段落')
        return
      }
      void fire({
        action,
        instruction: opts.instruction ?? '',
        scoringKey: opts.scoringKey ?? '',
        requirementKey: opts.requirementKey ?? '',
        selection,
      })
    },
    [editor, fire],
  )

  const retry = useCallback(
    (instruction: string) => {
      if (job) void fire({ ...job, instruction })
    },
    [job, fire],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    setPhase('idle')
    setResult(null)
    setStreamText('')
  }, [])

  const discard = useCallback(() => {
    setPhase('idle')
    setResult(null)
  }, [])

  /** 应用：文档未变则替换选区/插入光标处；返回是否成功 */
  const apply = useCallback((): boolean => {
    if (!editor || !job || !result) return false
    if (job.selection.doc !== editor.state.doc) {
      setError('文档在生成期间已被修改，请重新选择后再试')
      setPhase('idle')
      setResult(null)
      return false
    }
    const html = mdToHtml(result.content_md)
    const chain = editor
      .chain()
      .focus()
      .command(({ tr }) => {
        tr.setMeta('copilotApply', true)
        return true
      })
    if (job.action === 'star_response') chain.insertContentAt(job.selection.to, html).run()
    else chain.insertContentAt({ from: job.selection.from, to: job.selection.to }, html).run()
    markCopilotApplied(pid, result.request_id).catch(() => {
      /* 打点失败不打断编辑 */
    })
    setPhase('idle')
    setResult(null)
    return true
  }, [editor, job, result, pid])

  const clearError = useCallback(() => setError(''), [])

  return { phase, job, result, streamText, error, elapsed, start, retry, cancel, discard, apply, clearError }
}
