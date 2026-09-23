/** 通用编辑器工具栏（TipTap）：标题/粗斜体下划删除线/引用/分割线/列表/表格全套/撤销重做。
 *
 * 活跃态用 useEditorState 订阅（选区变化自动重渲染，不抖动整页）；
 * 光标进入表格时追加行列操作组。工作台润色页与起草章节编辑页共用。
 * StarterKit v3 已内置 Underline/Strike/Blockquote/HorizontalRule，无需额外扩展包。
 */
import { Button, Divider, Space, Tooltip } from 'antd'
import {
  BlockOutlined, BoldOutlined, ClearOutlined, DeleteColumnOutlined, DeleteRowOutlined,
  InsertRowBelowOutlined, InsertRowRightOutlined, ItalicOutlined, MinusOutlined,
  OrderedListOutlined, RedoOutlined, StrikethroughOutlined, TableOutlined, UnderlineOutlined,
  UndoOutlined, UnorderedListOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import { useEditorState, type Editor } from '@tiptap/react'

interface Props {
  editor: Editor | null
}

export default function EditorToolbar({ editor }: Props) {
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) =>
      e
        ? {
            h1: e.isActive('heading', { level: 1 }),
            h2: e.isActive('heading', { level: 2 }),
            h3: e.isActive('heading', { level: 3 }),
            bold: e.isActive('bold'),
            italic: e.isActive('italic'),
            underline: e.isActive('underline'),
            strike: e.isActive('strike'),
            quote: e.isActive('blockquote'),
            bullet: e.isActive('bulletList'),
            ordered: e.isActive('orderedList'),
            inTable: e.isActive('table'),
            headerRow: e.isActive('tableHeader'),
            canUndo: e.can().undo(),
            canRedo: e.can().redo(),
          }
        : null,
  })

  if (!editor || !s) return null
  const cmd = (fn: () => void) => () => {
    editor.chain().focus()
    fn()
  }
  const btn = (
    key: string,
    label: ReactNode,
    title: string,
    active: boolean,
    onClick: () => void,
    disabled = false,
  ) => (
    <Tooltip key={key} title={title}>
      <Button
        size="small" type="text"
        style={{
          minWidth: 28, padding: label && typeof label === 'string' ? '0 4px' : 0, fontWeight: 600,
          ...(active ? { background: '#e6f4ff', color: '#1677ff' } : {}),
        }}
        disabled={disabled}
        onMouseDown={(e) => e.preventDefault()} /* 防止点击按钮时编辑器失焦丢选区 */
        onClick={onClick}
      >
        {label}
      </Button>
    </Tooltip>
  )
  const c = editor.chain().focus()

  return (
    <div style={{ borderBottom: '1px solid #f0f0f0', padding: '2px 4px', marginBottom: 8, lineHeight: '24px' }}>
      <Space size={2} wrap>
        {btn('h1', 'H1', '一级标题', s.h1, cmd(() => c.toggleHeading({ level: 1 }).run()))}
        {btn('h2', 'H2', '二级标题', s.h2, cmd(() => c.toggleHeading({ level: 2 }).run()))}
        {btn('h3', 'H3', '三级标题', s.h3, cmd(() => c.toggleHeading({ level: 3 }).run()))}
        <Divider type="vertical" />
        {btn('bold', <BoldOutlined />, '粗体', s.bold, cmd(() => c.toggleBold().run()))}
        {btn('italic', <ItalicOutlined />, '斜体', s.italic, cmd(() => c.toggleItalic().run()))}
        {btn('underline', <UnderlineOutlined />, '下划线', s.underline, cmd(() => c.toggleUnderline().run()))}
        {btn('strike', <StrikethroughOutlined />, '删除线', s.strike, cmd(() => c.toggleStrike().run()))}
        <Divider type="vertical" />
        {btn('quote', <BlockOutlined />, '引用块', s.quote, cmd(() => c.toggleBlockquote().run()))}
        {btn('hr', <MinusOutlined />, '分割线', false, cmd(() => c.setHorizontalRule().run()))}
        {btn('clear', <ClearOutlined />, '清除格式', false, cmd(() => c.clearNodes().unsetAllMarks().run()))}
        <Divider type="vertical" />
        {btn('bullet', <UnorderedListOutlined />, '无序列表', s.bullet, cmd(() => c.toggleBulletList().run()))}
        {btn('ordered', <OrderedListOutlined />, '有序列表', s.ordered, cmd(() => c.toggleOrderedList().run()))}
        <Divider type="vertical" />
        {btn('table', <TableOutlined />, '插入 3×3 表格', false,
          cmd(() => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()))}
        {s.inTable && (
          <>
            {btn('addRow', <InsertRowBelowOutlined />, '下方加一行', false, cmd(() => c.addRowAfter().run()))}
            {btn('delRow', <DeleteRowOutlined />, '删除当前行', false, cmd(() => c.deleteRow().run()))}
            {btn('addCol', <InsertRowRightOutlined />, '右侧加一列', false, cmd(() => c.addColumnAfter().run()))}
            {btn('delCol', <DeleteColumnOutlined />, '删除当前列', false, cmd(() => c.deleteColumn().run()))}
            {btn('header', '表头', '切换表头行', s.headerRow, cmd(() => c.toggleHeaderRow().run()))}
            {btn('delTable', '删表', '删除整个表格', false, cmd(() => c.deleteTable().run()))}
          </>
        )}
        <Divider type="vertical" />
        {btn('undo', <UndoOutlined />, '撤销', false, cmd(() => c.undo().run()), !s.canUndo)}
        {btn('redo', <RedoOutlined />, '重做', false, cmd(() => c.redo().run()), !s.canRedo)}
      </Space>
    </div>
  )
}
