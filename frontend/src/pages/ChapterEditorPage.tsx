/** 章节编辑器页（Q20/Q24）：TipTap 富文本 + markdown 双向转换 + 版本历史。
 *
 * md → 编辑器：marked 转 HTML 注入；编辑器 → md：turndown 转回。
 * 保存 = 新版本快照（source=human），版本历史侧栏可查看 AI/人工版本链。
 * 段落级 AI 重写（Q8）预留：选中文本工具栏按钮后续接入。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Drawer, Space, Tag, Timeline, Typography, message } from 'antd'
import { HistoryOutlined, SaveOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import { marked } from 'marked'
import TurndownService from 'turndown'
import { getChapter, listChapterVersions, saveChapter, type ChapterContentOut } from '../api'

const turndown = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' })

const SOURCE_LABEL: Record<string, { label: string; color: string }> = {
  ai_chapter: { label: 'AI 整章', color: 'blue' },
  ai_paragraph: { label: 'AI 段落', color: 'geekblue' },
  human: { label: '人工', color: 'green' },
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

  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  const initialHtml = useMemo(() => {
    if (!chapter?.content_md) return ''
    return marked.parse(chapter.content_md, { async: false }) as string
  }, [chapter?.content_md])

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: '本章尚未起草，可直接编写或返回列表执行 AI 起草' }),
    ],
    content: initialHtml,
    onUpdate: () => setDirty(true),
  })

  // 数据到达后注入（首次或切换章节）
  useEffect(() => {
    if (editor && initialHtml && !editor.getText()) {
      editor.commands.setContent(initialHtml)
      setDirty(false)
    }
  }, [editor, initialHtml])

  const doSave = async () => {
    if (!editor) return
    setSaving(true)
    try {
      const md = turndown.turndown(editor.getHTML())
      const saved = await saveChapter(pid, cid, md)
      message.success(`已保存为 v${saved.version_no}（${saved.word_count} 字）`)
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['chapter', pid, cid] })
      qc.invalidateQueries({ queryKey: ['chapter-versions', pid, cid] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (isLoading || !chapter) return <Card loading />

  return (
    <Card
      title={
        <Space>
          <Typography.Text strong>{chapter.chapter_key} {chapter.title}</Typography.Text>
          <Tag>v{chapter.version_no}</Tag>
          {dirty && <Tag color="orange">未保存</Tag>}
        </Space>
      }
      extra={
        <Space>
          <Typography.Text type="secondary">
            {chapter.word_count} / {chapter.target_words} 字
          </Typography.Text>
          <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
            版本历史
          </Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={doSave}>
            保存
          </Button>
        </Space>
      }
    >
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
        <EditorContent editor={editor} />
      </div>

      <Drawer title="版本历史" open={historyOpen} onClose={() => setHistoryOpen(false)} width={360}>
        <Timeline
          items={(versions ?? []).map((v) => ({
            color: SOURCE_LABEL[v.source]?.color ?? 'gray',
            children: (
              <Space direction="vertical" size={0}>
                <Space>
                  <Typography.Text strong>v{v.version_no}</Typography.Text>
                  <Tag color={SOURCE_LABEL[v.source]?.color}>{SOURCE_LABEL[v.source]?.label ?? v.source}</Tag>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {v.word_count} 字 · {v.created_at ? new Date(v.created_at).toLocaleString() : ''}
                </Typography.Text>
              </Space>
            ),
          }))}
        />
      </Drawer>
    </Card>
  )
}
