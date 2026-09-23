/** 标书工作台·润色页：左章节树（可勾选批量）· 中编辑器（含段落级 Copilot）· 右整章润色面板。
 *
 * 两种粒度并存（定稿决策）：
 * - 整章：右侧选润色目标 → streamCopilot(action=polish)（服务端自取全文）→ diff 对照 → 采纳/拒绝/重新生成；
 *   采纳 = saveChapter(source_hint=ai_paragraph) 直接落新版本，并回打 applied。
 * - 段落：编辑器内选中文字 → CopilotBubble 六动作（复用既有 useCopilot 全链路）。
 * 批量队列：左树勾选多章 → 顺序逐章流式润色（互不并发），逐章审阅采纳。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Input, Popconfirm, Radio, Select, Space, Spin, Tag, Tree, Typography, message,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, MenuFoldOutlined, MenuUnfoldOutlined, RedoOutlined,
  SaveOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import { TableKit } from '@tiptap/extension-table'
import type { DataNode } from 'antd/es/tree'
import diff_match_patch from 'diff-match-patch'
import {
  getAnalysis, getChapter, getProject, listChapters, markCopilotApplied, saveChapter, streamCopilot,
  type ChapterOut, type CopilotResponse,
} from '../api'
import { htmlToMd, mdToHtml } from '../lib/markdown'
import WorkbenchNav from '../components/workbench/WorkbenchNav'
import EditorToolbar from '../components/editor/EditorToolbar'
import CopilotBubble from '../components/copilot/CopilotBubble'
import { useCopilot } from '../components/copilot/useCopilot'

const POLISH_GOALS = [
  { value: 'professional', label: '更专业', instruction: '提升用词与表达的专业度，消除口语化和空泛表述' },
  { value: 'concise', label: '更简洁', instruction: '删除冗余与重复，使行文紧凑，保持全部技术要点与参数不变' },
  { value: 'specific', label: '更具体', instruction: '把笼统表述具体化：补充可执行的措施、步骤与量化描述，不得虚构事实' },
  { value: 'advantage', label: '突出优势', instruction: '在不改变事实的前提下，强化我方方案优势与技术亮点的表达与说服力' },
  { value: 'align', label: '对齐评分点', instruction: '使正文逐条呼应指定评分标准的每个评分要素，要素响应表述明确可对照', needScoring: true },
] as const

type GoalValue = (typeof POLISH_GOALS)[number]['value']

interface QueueItem {
  chapterId: number
  chapterKey: string
  title: string
  status: 'pending' | 'streaming' | 'done' | 'error'
  streamText: string
  result: CopilotResponse | null
  error: string
}

function buildTree(chapters: ChapterOut[]): DataNode[] {
  // chapter_key 形如 9.7.1.2：按段合成父链（父节点仅展示编号），叶节点挂章节
  const root: DataNode[] = []
  const nodes = new Map<string, DataNode>()
  const getOrCreate = (path: string, title?: string): DataNode => {
    if (nodes.has(path)) return nodes.get(path)!
    const segs = path.split('.')
    const parent = segs.length > 1 ? getOrCreate(segs.slice(0, -1).join('.')) : null
    const node: DataNode = { key: path, title: title ?? path, children: [] }
    ;(parent ? (parent.children as DataNode[]) : root).push(node)
    nodes.set(path, node)
    return node
  }
  for (const ch of chapters) {
    getOrCreate(ch.chapter_key).title = `${ch.chapter_key} ${ch.title}`
  }
  return root
}

/** 双栏 diff：原文红删除线 / 润色绿（同 CopilotPreview 的语义 diff 口径） */
function DiffView({ before, after }: { before: string; after: string }) {
  const parts = useMemo(() => {
    const dmp = new diff_match_patch()
    const diffs = dmp.diff_main(before, after)
    dmp.diff_cleanupSemantic(diffs)
    return diffs
  }, [before, after])
  return (
    <div style={{ maxHeight: 320, overflow: 'auto', fontSize: 12, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
      {parts.map(([op, text], i) =>
        op === 0 ? (
          <span key={i}>{text}</span>
        ) : op < 0 ? (
          <span key={i} style={{ background: '#fff1f0', color: '#cf1322', textDecoration: 'line-through' }}>{text}</span>
        ) : (
          <span key={i} style={{ background: '#f6ffed', color: '#389e0d' }}>{text}</span>
        ),
      )}
    </div>
  )
}

export default function WbPolishPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const qc = useQueryClient()

  const { data: project } = useQuery({ queryKey: ['project', pid], queryFn: () => getProject(pid) })
  const { data: chapters } = useQuery({ queryKey: ['chapters', pid], queryFn: () => listChapters(pid) })
  const { data: analysis } = useQuery({ queryKey: ['analysis', pid], queryFn: () => getAnalysis(pid) })

  const [currentId, setCurrentId] = useState<number | null>(null)
  const [checkedIds, setCheckedIds] = useState<number[]>([])
  const [goal, setGoal] = useState<GoalValue>('professional')
  const [custom, setCustom] = useState('')
  const [scoringKey, setScoringKey] = useState<string | undefined>()
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [queueRunning, setQueueRunning] = useState(false)
  const queueStop = useRef(false)
  // 两侧侧边栏：左（章节树）默认展开，右（整章润色）默认收缩，偏好各自记忆在本地
  const [treeOpen, setTreeOpen] = useState(() => localStorage.getItem('wb-tree-open') !== '0')
  const toggleTree = () => {
    setTreeOpen((v) => {
      localStorage.setItem('wb-tree-open', v ? '0' : '1')
      return !v
    })
  }
  const [polishOpen, setPolishOpen] = useState(() => localStorage.getItem('wb-polish-open') === '1')
  const togglePolish = () => {
    setPolishOpen((v) => {
      localStorage.setItem('wb-polish-open', v ? '0' : '1')
      return !v
    })
  }

  useEffect(() => {
    if (currentId === null && chapters && chapters.length) setCurrentId(chapters[0].id)
  }, [chapters, currentId])

  const { data: chapter, isLoading } = useQuery({
    queryKey: ['chapter', pid, currentId],
    queryFn: () => getChapter(pid, currentId!),
    enabled: currentId !== null,
  })

  // ---- 编辑器（同 ChapterEditorPage 口径：表格 Kit 必备，md↔html 往返无损） ----
  const [dirty, setDirty] = useState(false)
  const [lastSource, setLastSource] = useState<'human' | 'ai_paragraph'>('human')
  const [saving, setSaving] = useState(false)
  const initialHtml = useMemo(() => (chapter?.content_md ? mdToHtml(chapter.content_md) : ''), [chapter?.content_md])

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: '本章暂无正文' }),
      TableKit.configure({ table: { resizable: false } }),
    ],
    content: initialHtml,
    onUpdate: ({ transaction }) => {
      setDirty(true)
      setLastSource(transaction.getMeta('copilotApply') ? 'ai_paragraph' : 'human')
    },
  })

  const copilot = useCopilot(pid, currentId ?? 0, editor)
  useEffect(() => {
    if (copilot.error) {
      message.error(copilot.error)
      copilot.clearError()
    }
  }, [copilot.error])

  // 章节切换/采纳后重灌编辑器：本页编辑器跨章节持久（ChapterEditorPage 每章新挂载，
  // 那边的"仅在空时注入"条件在这里永远不成立，切章节不会换内容——按章节+版本签名判断）。
  // setContent 单参调用不触发 onUpdate，不会误置 dirty。
  const loadedSigRef = useRef('')
  const chapterSig = chapter ? `${chapter.id}:${chapter.version_no}:${chapter.content_md.length}` : ''
  useEffect(() => {
    if (!editor || !chapterSig || loadedSigRef.current === chapterSig) return
    loadedSigRef.current = chapterSig
    editor.commands.setContent(initialHtml || '<p></p>')
    setDirty(false)
    setLastSource('human')
  }, [editor, chapterSig, initialHtml])

  const doSave = async () => {
    if (!editor || currentId === null) return
    setSaving(true)
    try {
      const md = htmlToMd(editor.getHTML())
      const saved = await saveChapter(pid, currentId, md, lastSource)
      message.success(`已保存为 v${saved.version_no}（${saved.word_count} 字）`)
      setDirty(false)
      setLastSource('human')
      qc.invalidateQueries({ queryKey: ['chapter', pid, currentId] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // ---- 润色指令组装 ----
  const goalMeta = POLISH_GOALS.find((g) => g.value === goal)!
  const buildInstruction = () => {
    const parts: string[] = []
    if (goal === 'align' && scoringKey) {
      const it = analysis?.scoring_items.find((s) => s.item_key === scoringKey)
      if (it) parts.push(`对齐评分点 ${it.item_key} ${it.item}（${it.score} 分）：${it.criteria_original}`)
    }
    parts.push(goalMeta.instruction)
    if (custom.trim()) parts.push(`补充要求：${custom.trim()}`)
    return parts.join('\n')
  }

  // ---- 单章 / 批量润色（同一执行核：顺序流式，不并发） ----
  const polishOne = async (item: QueueItem, onDelta: (t: string) => void): Promise<CopilotResponse> => {
    const patch = (p: Partial<QueueItem>) =>
      setQueue((prev) => prev.map((q) => (q.chapterId === item.chapterId ? { ...q, ...p } : q)))
    patch({ status: 'streaming', streamText: '', error: '' })
    try {
      const resp = await streamCopilot(
        pid, item.chapterId,
        { action: 'polish', instruction: buildInstruction(), ...(goal === 'align' && scoringKey ? { scoring_key: scoringKey } : {}) },
        { onDelta: (t) => onDelta(t) },
      )
      patch({ status: 'done', result: resp })
      return resp
    } catch (e) {
      patch({ status: 'error', error: e instanceof Error ? e.message : '润色失败' })
      throw e
    }
  }

  const startSingle = async () => {
    if (currentId === null || !chapter) return
    const item: QueueItem = {
      chapterId: currentId, chapterKey: chapter.chapter_key, title: chapter.title,
      status: 'pending', streamText: '', result: null, error: '',
    }
    setQueue([item])
    setPolishOpen(true) // 触发润色自动展开面板，流式过程可见
    try {
      await polishOne(item, (t) =>
        setQueue((prev) => prev.map((q) => (q.chapterId === item.chapterId ? { ...q, streamText: q.streamText + t } : q))),
      )
    } catch {
      /* 错误已入队展示 */
    }
  }

  const startBatch = async () => {
    const targets = (chapters ?? []).filter((c) => checkedIds.includes(c.id) && c.word_count > 0)
    if (!targets.length) return message.warning('请先在左侧勾选有正文的章节')
    queueStop.current = false
    const items: QueueItem[] = targets.map((c) => ({
      chapterId: c.id, chapterKey: c.chapter_key, title: c.title,
      status: 'pending', streamText: '', result: null, error: '',
    }))
    setQueue(items)
    setQueueRunning(true)
    setPolishOpen(true) // 批量队列启动自动展开面板
    try {
      for (const item of items) {
        if (queueStop.current) break
        try {
          await polishOne(item, (t) =>
            setQueue((prev) => prev.map((q) => (q.chapterId === item.chapterId ? { ...q, streamText: q.streamText + t } : q))),
          )
        } catch {
          /* 单章失败继续下一章 */
        }
      }
    } finally {
      setQueueRunning(false)
    }
  }

  const adopt = async (item: QueueItem) => {
    if (!item.result) return
    try {
      const saved = await saveChapter(pid, item.chapterId, item.result.content_md, 'ai_paragraph')
      markCopilotApplied(pid, item.result.request_id).catch(() => undefined)
      message.success(`${item.chapterKey} 已采纳为 v${saved.version_no}（${saved.word_count} 字）`)
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
      qc.invalidateQueries({ queryKey: ['chapter', pid, item.chapterId] })
      setQueue((prev) => prev.filter((q) => q.chapterId !== item.chapterId))
    } catch (e) {
      message.error(e instanceof Error ? e.message : '采纳失败')
    }
  }

  const discard = (item: QueueItem) => setQueue((prev) => prev.filter((q) => q.chapterId !== item.chapterId))
  const regenerate = async (item: QueueItem) => {
    try {
      await polishOne({ ...item, streamText: '' }, (t) =>
        setQueue((prev) => prev.map((q) => (q.chapterId === item.chapterId ? { ...q, streamText: q.streamText + t } : q))),
      )
    } catch {
      /* 已入队 */
    }
  }

  // ---- 左树 ----
  const treeData = useMemo(() => buildTree(chapters ?? []), [chapters])
  const idByKey = useMemo(() => new Map((chapters ?? []).map((c) => [c.chapter_key, c.id])), [chapters])
  const checkedKeys = useMemo(
    () => (chapters ?? []).filter((c) => checkedIds.includes(c.id)).map((c) => c.chapter_key),
    [chapters, checkedIds],
  )
  const currentChapterKey = useMemo(
    () => (chapters ?? []).find((c) => c.id === currentId)?.chapter_key ?? '',
    [chapters, currentId],
  )
  const busy = copilot.phase !== 'idle' || queueRunning
  const goalNeedsScoring = goal === 'align'

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <WorkbenchNav pid={pid} active="polish" />
      {project && project.state !== 'wb_ready' && (
        <Alert type="warning" showIcon message={`项目状态 ${project.state}：请先在导入页完成目录确认`} />
      )}
      <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
        {/* 左：章节侧边栏（默认收缩） */}
        <div
          style={{
            width: treeOpen ? 268 : 44, transition: 'width .2s', flexShrink: 0,
            border: '1px solid #f0f0f0', borderRadius: 6, background: '#fff',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}
        >
          {treeOpen ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 8px', borderBottom: '1px solid #f0f0f0' }}>
                <Typography.Text strong style={{ fontSize: 13 }}>
                  章节（{chapters?.length ?? 0}）
                </Typography.Text>
                <Button size="small" type="text" icon={<MenuFoldOutlined />} onClick={toggleTree} title="收缩章节栏" />
              </div>
              <div style={{ flex: 1, overflow: 'auto', padding: 4 }}>
                <Tree
                  treeData={treeData}
                  defaultExpandAll
                  blockNode
                  checkable
                  checkedKeys={checkedKeys}
                  onCheck={(keys) =>
                    setCheckedIds(
                      (keys as string[]).map((k) => idByKey.get(k)).filter((v): v is number => v !== undefined),
                    )
                  }
                  selectedKeys={currentChapterKey ? [currentChapterKey] : []}
                  onSelect={(keys) => {
                    const cid = idByKey.get(String(keys[0]))
                    if (cid) setCurrentId(cid)
                  }}
                />
              </div>
            </>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: 6, gap: 8 }}>
              <Button size="small" type="text" icon={<MenuUnfoldOutlined />} onClick={toggleTree} title="展开章节栏" />
              <div style={{ writingMode: 'vertical-rl', letterSpacing: 4, color: '#8c8c8c', fontSize: 12 }}>
                章节 {chapters?.length ?? 0}
              </div>
              {checkedIds.length > 0 && <Tag color="blue" style={{ margin: 0 }}>{checkedIds.length}</Tag>}
            </div>
          )}
        </div>

        {/* 中：编辑器 */}
        <Card
          size="small" style={{ flex: 1, minWidth: 0 }}
          title={
            chapter ? (
              <Space>
                <Typography.Text strong ellipsis style={{ maxWidth: 320 }}>{chapter.chapter_key} {chapter.title}</Typography.Text>
                <Tag>v{chapter.version_no}</Tag>
                <Tag color="default">{chapter.word_count} 字</Tag>
                {dirty && <Tag color="orange">未保存</Tag>}
              </Space>
            ) : '正文编辑'
          }
          extra={
            <Button size="small" type="primary" icon={<SaveOutlined />} disabled={!dirty} loading={saving} onClick={doSave}>
              保存
            </Button>
          }
        >
          {isLoading || !chapter ? (
            <Spin />
          ) : (
            <div className="chapter-editor" style={{ border: '1px solid #d9d9d9', borderRadius: 6, padding: 12, minHeight: 560 }}>
              <EditorToolbar editor={editor} />
              <CopilotBubble
                editor={editor}
                enabled={!busy}
                scoringItems={analysis?.scoring_items ?? []}
                defaultScoringKey={analysis?.scoring_items.find((s) => s.category === '技术')?.item_key ?? ''}
                onAction={(action, opts) => copilot.start(action, opts)}
              />
              <EditorContent editor={editor} />
            </div>
          )}
        </Card>

        {/* 右：润色面板 */}
        {/* 右：整章润色侧边栏（默认收缩） */}
        <div
          style={{
            width: polishOpen ? 420 : 44, transition: 'width .2s', flexShrink: 0,
            border: '1px solid #f0f0f0', borderRadius: 6, background: '#fff',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}
        >
          {polishOpen ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 8px', borderBottom: '1px solid #f0f0f0' }}>
                <Typography.Text strong style={{ fontSize: 13 }}>整章润色</Typography.Text>
                <Button size="small" type="text" icon={<MenuFoldOutlined />} onClick={togglePolish} title="收缩润色面板" />
              </div>
              <div style={{ flex: 1, maxHeight: 640, overflow: 'auto', padding: 12 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">润色目标</Typography.Text>
              <Radio.Group
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}
                disabled={busy}
              >
                {POLISH_GOALS.map((g) => (
                  <Radio key={g.value} value={g.value}>{g.label}</Radio>
                ))}
              </Radio.Group>
            </div>
            {goalNeedsScoring && (
              <Select
                style={{ width: '100%' }}
                placeholder="选择要对齐的技术评分点"
                showSearch optionFilterProp="label"
                value={scoringKey}
                onChange={setScoringKey}
                options={(analysis?.scoring_items ?? [])
                  .filter((s) => s.category === '技术')
                  .map((s) => ({ label: `${s.item_key} ${s.item}（${s.score} 分）`, value: s.item_key }))}
              />
            )}
            <Input.TextArea
              rows={2} placeholder="补充要求（可选），如：开头加一段方案总述" value={custom}
              onChange={(e) => setCustom(e.target.value)} disabled={busy}
            />
            <Space>
              <Button type="primary" icon={<ThunderboltOutlined />} loading={busy} onClick={startSingle} disabled={!chapter}>
                润色本章
              </Button>
              <Popconfirm
                title={`批量润色 ${checkedIds.length} 章`}
                description="逐章流式生成（不并发），完成后逐章审阅采纳；生成不落正文，采纳才写版本"
                onConfirm={startBatch}
                okText="开始批量"
                cancelText="取消"
                disabled={busy || !checkedIds.length}
              >
                <Button disabled={busy || !checkedIds.length}>批量润色（{checkedIds.length} 章）</Button>
              </Popconfirm>
              {queueRunning && (
                <Button danger size="small" onClick={() => { queueStop.current = true }}>停止队列</Button>
              )}
            </Space>

            {queue.length === 0 ? (
              <Typography.Text type="secondary">
                左侧勾选多章可批量润色；编辑器内选中文字可用段落级动作（重写/扩写/压缩…）。
              </Typography.Text>
            ) : (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {queue.map((item) => {
                  return (
                    <Card
                      key={item.chapterId} size="small"
                      title={<Typography.Text ellipsis style={{ maxWidth: 200 }}>{item.chapterKey} {item.title}</Typography.Text>}
                      extra={
                        item.status === 'streaming' ? <Tag color="processing">生成中…</Tag>
                        : item.status === 'done' ? <Tag color="success">待审阅</Tag>
                        : item.status === 'error' ? <Tag color="error">失败</Tag>
                        : <Tag>排队</Tag>
                      }
                    >
                      {item.status === 'streaming' && (
                        <div style={{ maxHeight: 180, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap', background: '#fafafa', padding: 8 }}>
                          {item.streamText || '等待模型首字…'}
                        </div>
                      )}
                      {item.status === 'error' && <Alert type="error" message={item.error} />}
                      {item.status === 'done' && item.result && (
                        <>
                          {item.result.warnings.length > 0 && (
                            <Alert type="warning" message={item.result.warnings.join('；')} style={{ marginBottom: 8 }} />
                          )}
                          <DiffBlock chapterId={item.chapterId} pid={pid} after={item.result.content_md} />
                          <Space style={{ marginTop: 8 }}>
                            <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => adopt(item)}>采纳</Button>
                            <Button size="small" icon={<CloseOutlined />} onClick={() => discard(item)}>拒绝</Button>
                            <Button size="small" icon={<RedoOutlined />} onClick={() => regenerate(item)} disabled={busy}>重新生成</Button>
                          </Space>
                        </>
                      )}
                    </Card>
                  )
                })}
              </Space>
            )}
          </Space>
              </div>
            </>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: 6, gap: 8 }}>
              <Button
                size="small" type="text" icon={<MenuUnfoldOutlined />}
                onClick={togglePolish} title="展开润色面板"
              />
              <div style={{ writingMode: 'vertical-rl', letterSpacing: 4, color: '#8c8c8c', fontSize: 12 }}>
                整章润色
              </div>
              {(queueRunning || queue.length > 0) && (
                <Tag color={queueRunning ? 'processing' : 'orange'} style={{ margin: 0 }}>
                  {queueRunning ? `${queue.filter((q) => q.status === 'streaming').length}/${queue.length}` : queue.length}
                </Tag>
              )}
            </div>
          )}
        </div>
      </div>
    </Space>
  )
}

/** 采纳前的原文对照：拉取章节当前正文做 diff（批量场景下非当前章也要能对照） */
function DiffBlock({ pid, chapterId, after }: { pid: number; chapterId: number; after: string }) {
  const { data } = useQuery({
    queryKey: ['chapter', pid, chapterId],
    queryFn: () => getChapter(pid, chapterId),
    staleTime: 60_000,
  })
  if (!data) return <Spin size="small" />
  return <DiffView before={data.content_md} after={after} />
}
