/** 章节编辑器页（Q20/Q24/Q8）：TipTap 富文本 + markdown 双向转换 + 版本历史 + 章节内 Copilot。
 *
 * md → 编辑器：marked 转 HTML 注入；编辑器 → md：turndown 转回。
 * 保存 = 新版本快照；来源判定看最后一次内容变更：应用 Copilot 结果后未再手改 → ai_paragraph，否则 human。
 * Copilot：选中文本 → 浮动动作条 → 预览对照 → 应用（选区替换）/ 放弃 / 带指令重试；★响应从顶栏入口插入光标处。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Drawer, Space, Spin, Tag, Timeline, Typography, message } from 'antd'
import { HistoryOutlined, SaveOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import { TableKit } from '@tiptap/extension-table'
import {
  COPILOT_ACTION_LABEL,
  getAnalysis,
  getChapter,
  listChapterVersions,
  saveChapter,
  type ChapterContentOut,
  type SaveSourceHint,
} from '../api'
import CopilotBubble from '../components/copilot/CopilotBubble'
import CopilotPreview from '../components/copilot/CopilotPreview'
import StarResponseModal from '../components/copilot/StarResponseModal'
import { useCopilot } from '../components/copilot/useCopilot'
import { htmlToMd, mdToHtml } from '../lib/markdown'

const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  ai_chapter: { label: 'AI 整章', color: 'blue' },
  ai_paragraph: { label: 'AI 段落', color: 'geekblue' },
  human: { label: '人工', color: 'green' },
}

interface VersionGroup {
  source: string
  count: number
  latest: number
  earliest: number
  word_count: number
  created_at: string
}

export default function ChapterEditorPage() {
  const { id, chapterId } = useParams<{ id: string; chapterId: string }>()
  const pid = Number(id)
  const cid = Number(chapterId)
  const qc = useQueryClient()

  const { data: chapter, isLoading } = useQuery<ChapterContentOut>({
    queryKey: ['chapter', pid, cid],
    queryFn: () => getChapter(pid, cid),
  })
  const { data: versions } = useQuery({
    queryKey: ['chapter-versions', pid, cid],
    queryFn: () => listChapterVersions(pid, cid),
  })
  const { data: analysis } = useQuery({
    queryKey: ['analysis', pid],
    queryFn: () => getAnalysis(pid),
  })

  const [dirty, setDirty] = useState(false)
  const [lastSource, setLastSource] = useState<SaveSourceHint>('human')
  const [saving, setSaving] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [starOpen, setStarOpen] = useState(false)

  const initialHtml = useMemo(() => {
    if (!chapter?.content_md) return ''
    return mdToHtml(chapter.content_md)
  }, [chapter?.content_md])

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: '本章尚未起草，可直接编写或返回列表执行 AI 起草' }),
      // 标书正文表格密集（参数表/响应对照表），StarterKit 不含表格，缺了会在加载/保存时被拍平成文本
      TableKit.configure({ table: { resizable: false } }),
    ],
    content: initialHtml,
    onUpdate: ({ transaction }) => {
      setDirty(true)
      setLastSource(transaction.getMeta('copilotApply') ? 'ai_paragraph' : 'human')
    },
  })

  const copilot = useCopilot(pid, cid, editor)
  const { error: copilotError, clearError: clearCopilotError } = copilot
  useEffect(() => {
    if (!copilotError) return
    message.error(copilotError)
    clearCopilotError()
  }, [copilotError, clearCopilotError])

  // 数据到达后注入（首次或切换章节）
  useEffect(() => {
    if (editor && initialHtml && !editor.getText()) {
      editor.commands.setContent(initialHtml)
      setDirty(false)
      setLastSource('human')
    }
  }, [editor, initialHtml])

  const doSave = async () => {
    if (!editor) return
    setSaving(true)
    try {
      const md = htmlToMd(editor.getHTML())
      const saved = await saveChapter(pid, cid, md, lastSource)
      message.success(`已保存为 v${saved.version_no}（${saved.word_count} 字）`)
      setDirty(false)
      setLastSource('human')
      qc.invalidateQueries({ queryKey: ['chapter', pid, cid] })
      qc.invalidateQueries({ queryKey: ['chapter-versions', pid, cid] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // 评分点选项：本章挂接的排前面，默认选第一个
  const chapterKeys = useMemo(() => (chapter?.scoring_keys ?? '').split(',').filter(Boolean), [chapter?.scoring_keys])
  const scoringOptions = useMemo(() => {
    const items = analysis?.scoring_items ?? []
    const mine = items.filter((s) => chapterKeys.includes(s.item_key))
    const others = items.filter((s) => !chapterKeys.includes(s.item_key))
    return [...mine, ...others]
  }, [analysis?.scoring_items, chapterKeys])

  // 版本历史：连续同源版本折叠成一组，防 AI 段落刷屏
  const versionGroups = useMemo(() => {
    const groups: VersionGroup[] = []
    for (const v of versions ?? []) {
      const last = groups[groups.length - 1]
      if (last && last.source === v.source) {
        last.count += 1
        last.earliest = v.version_no
      } else {
        groups.push({
          source: v.source,
          count: 1,
          latest: v.version_no,
          earliest: v.version_no,
          word_count: v.word_count,
          created_at: v.created_at,
        })
      }
    }
    return groups
  }, [versions])

  if (isLoading || !chapter) return <Card loading />

  const busy = copilot.phase !== 'idle'

  return (
    <Card
      title={
        <Space>
          <Typography.Text strong>{chapter.chapter_key} {chapter.title}</Typography.Text>
          <Tag>v{chapter.version_no}</Tag>
          {dirty && <Tag color="orange">未保存</Tag>}
          {dirty && lastSource === 'ai_paragraph' && <Tag color="geekblue">含 AI 段落修改</Tag>}
        </Space>
      }
      extra={
        <Space>
          <Typography.Text type="secondary">
            {chapter.word_count} / {chapter.target_words} 字
          </Typography.Text>
          <Button icon={<ThunderboltOutlined />} disabled={busy} onClick={() => setStarOpen(true)}>
            插入★响应
          </Button>
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
            版本历史
          </Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={doSave}>
            保存
          </Button>
        </Space>
      }
    >
      {copilot.phase === 'loading' && copilot.job && (
        <Alert
          type="info"
          showIcon
          icon={<Spin size="small" />}
          style={{ marginBottom: 12 }}
          message={`AI ${COPILOT_ACTION_LABEL[copilot.job.action]}中… 已等待 ${copilot.elapsed}s`}
          action={
            <Button size="small" onClick={copilot.cancel}>
              取消
            </Button>
          }
        />
      )}

      <div
        className="chapter-editor"
        style={{
          border: '1px solid #d9d9d9',
          borderRadius: 8,
          padding: '16px 24px',
          minHeight: 480,
          background: '#fff',
        }}
      >
        {editor && (
          <CopilotBubble
            editor={editor}
            enabled={!busy}
            scoringItems={scoringOptions}
            defaultScoringKey={chapterKeys[0] ?? ''}
            onAction={(action, opts) => copilot.start(action, opts)}
          />
        )}
        <EditorContent editor={editor} />
      </div>

      <CopilotPreview
        phase={copilot.phase}
        job={copilot.job}
        streamText={copilot.streamText}
        result={copilot.result}
        onApply={() => {
          if (copilot.apply()) message.success('已应用到编辑器，记得保存')
        }}
        onDiscard={copilot.discard}
        onRetry={copilot.retry}
        onCancel={copilot.cancel}
      />

      <StarResponseModal
        open={starOpen}
        requirements={analysis?.tech_requirements ?? []}
        onCancel={() => setStarOpen(false)}
        onSubmit={(requirementKey, instruction) => {
          setStarOpen(false)
          copilot.start('star_response', { requirementKey, instruction })
        }}
      />

      <Drawer title="版本历史" open={historyOpen} onClose={() => setHistoryOpen(false)} width={360}>
        <Timeline
          items={versionGroups.map((g) => ({
            color: SOURCE_LABEL[g.source]?.color ?? 'gray',
            children: (
              <Space direction="vertical" size={0}>
                <Space>
                  <Typography.Text strong>
                    v{g.latest}
                    {g.count > 1 && <Typography.Text type="secondary"> ← v{g.earliest}（{g.count} 次）</Typography.Text>}
                  </Typography.Text>
                  <Tag color={SOURCE_LABEL[g.source]?.color}>{SOURCE_LABEL[g.source]?.label ?? g.source}</Tag>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {g.word_count} 字 · {g.created_at ? new Date(g.created_at).toLocaleString() : ''}
                </Typography.Text>
              </Space>
            ),
          }))}
        />
      </Drawer>
    </Card>
  )
}
