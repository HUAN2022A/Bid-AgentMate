/** 预览-处置浮层：streaming 形态单栏打字机（自动滚动+光标）→ preview 形态双栏 diff 对照
 *（diff-match-patch 高亮）+ 溯源标签 + 软校验提示 + 应用/放弃/带指令重试。 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Alert, Button, Col, Input, Modal, Row, Space, Tag, Typography } from 'antd'
import { DIFF_DELETE, DIFF_INSERT, diff_match_patch } from 'diff-match-patch'
import { COPILOT_ACTION_LABEL, type CopilotResponse } from '../../api'
import type { CopilotJob, CopilotPhase } from './useCopilot'

const paneStyle: CSSProperties = {
  whiteSpace: 'pre-wrap',
  lineHeight: 1.7,
  maxHeight: 420,
  overflow: 'auto',
  padding: 12,
  border: '1px solid #f0f0f0',
  borderRadius: 6,
  background: '#fafafa',
  marginTop: 4,
}

/** 打字机闪烁光标（零依赖：内联 keyframes，随 streaming 形态挂载/卸载） */
const CURSOR_KEYFRAMES =
  '@keyframes copilot-cursor-blink { 0%, 100% { opacity: 1 } 50% { opacity: 0 } }'
const cursorStyle: CSSProperties = {
  display: 'inline-block',
  width: 2,
  height: '1em',
  marginLeft: 2,
  background: '#1677ff',
  verticalAlign: 'text-bottom',
  animation: 'copilot-cursor-blink 1s step-end infinite',
}

function DiffPane({ oldText, newText, side }: { oldText: string; newText: string; side: 'old' | 'new' }) {
  const diffs = useMemo(() => {
    if (!oldText) return null
    const dmp = new diff_match_patch()
    const d = dmp.diff_main(oldText, newText)
    dmp.diff_cleanupSemantic(d)
    return d
  }, [oldText, newText])

  if (!diffs) return <div style={paneStyle}>{side === 'old' ? oldText : newText}</div>
  return (
    <div style={paneStyle}>
      {diffs.map(([op, text], i) => {
        if (op === DIFF_DELETE) {
          return side === 'old' ? (
            <span key={i} style={{ background: '#ffd8d6', textDecoration: 'line-through' }}>{text}</span>
          ) : null
        }
        if (op === DIFF_INSERT) {
          return side === 'new' ? <span key={i} style={{ background: '#d9f7be' }}>{text}</span> : null
        }
        return <span key={i}>{text}</span>
      })}
    </div>
  )
}

/** 重试栏：按 request_id 重建，每次新结果到达时指令框回到该次任务的指令 */
function RetryBar({ initial, onRetry }: { initial: string; onRetry: (instruction: string) => void }) {
  const [instruction, setInstruction] = useState(initial)
  return (
    <Space.Compact style={{ width: '100%' }}>
      <Input
        placeholder="不满意？补充指令后重试，如：语气更正式、突出售后本地化"
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onPressEnter={() => onRetry(instruction.trim())}
      />
      <Button onClick={() => onRetry(instruction.trim())}>带指令重试</Button>
    </Space.Compact>
  )
}

interface Props {
  phase: CopilotPhase
  job: CopilotJob | null
  /** streaming 阶段逐 delta 累积的文本（preview 阶段渲染以 result.content_md 为准） */
  streamText: string
  result: CopilotResponse | null
  onApply: () => void
  onDiscard: () => void
  onRetry: (instruction: string) => void
  /** 停止生成（loading 取消 / streaming 中断流）：abort 当前请求 */
  onCancel: () => void
}

export default function CopilotPreview({
  phase, job, streamText, result, onApply, onDiscard, onRetry, onCancel,
}: Props) {
  const paneRef = useRef<HTMLDivElement | null>(null)
  const isStreaming = phase === 'streaming'
  // 打字机自动滚动：新文本到达即贴到底部（仅 streaming 形态挂载）
  useEffect(() => {
    if (!isStreaming) return
    const el = paneRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [streamText, isStreaming])

  if (!job) return null
  const open = isStreaming || phase === 'preview'
  const isInsert = job.action === 'star_response'

  return (
    <Modal
      open={open}
      width={980}
      title={`AI 建议 · ${COPILOT_ACTION_LABEL[job.action]}`}
      onCancel={isStreaming ? onCancel : onDiscard}
      maskClosable={false}
      footer={
        isStreaming ? (
          <Button onClick={onCancel}>停止生成</Button>
        ) : (
          <Space>
            <Button onClick={onDiscard}>放弃</Button>
            <Button type="primary" onClick={onApply}>
              {isInsert ? '插入到光标处' : '应用（替换选区）'}
            </Button>
          </Space>
        )
      }
    >
      {isStreaming ? (
        <Row>
          <style>{CURSOR_KEYFRAMES}</style>
          <Col span={24}>
            <Typography.Text type="secondary">AI 建议（生成中）</Typography.Text>
            <div ref={paneRef} style={paneStyle}>
              {streamText}
              <span style={cursorStyle} />
            </div>
          </Col>
        </Row>
      ) : result ? (
        <PreviewBody job={job} result={result} onRetry={onRetry} />
      ) : null}
    </Modal>
  )
}

/** preview 形态主体：溯源标签 + 软校验 + 双栏 diff + 带指令重试（done 后 result 齐备） */
function PreviewBody({
  job, result, onRetry,
}: { job: CopilotJob; result: CopilotResponse; onRetry: (i: string) => void }) {
  const isInsert = job.action === 'star_response'
  const refs = result.context_refs
  const materialCount = refs.material_ids?.length ?? 0

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap size={4}>
        {refs.scoring_keys?.map((k) => (
          <Tag key={k} color="blue">评分点 {k}</Tag>
        ))}
        {refs.requirement_key && <Tag color="purple">技术要求 {refs.requirement_key}</Tag>}
        <Tag color={materialCount ? 'green' : 'default'}>素材卡 {materialCount} 张</Tag>
        {job.instruction && <Tag>指令：{job.instruction}</Tag>}
      </Space>

      {result.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {result.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          }
        />
      )}

      <Row gutter={12}>
        {!isInsert && (
          <Col span={12}>
            <Typography.Text type="secondary">原文</Typography.Text>
            <DiffPane oldText={job.selection.md} newText={result.content_md} side="old" />
          </Col>
        )}
        <Col span={isInsert ? 24 : 12}>
          <Typography.Text type="secondary">AI 建议</Typography.Text>
          <DiffPane oldText={isInsert ? '' : job.selection.md} newText={result.content_md} side="new" />
        </Col>
      </Row>

      <RetryBar key={result.request_id} initial={job.instruction} onRetry={onRetry} />
    </Space>
  )
}
