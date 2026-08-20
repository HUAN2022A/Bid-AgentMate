/** 大纲确认页（Q27）：左树编辑 + 右评分点挂接展示，底部确认按钮。
 *
 * 编辑能力：改标题、改目标字数、增删子章、勾选挂接评分点；随改随存（防抖 1s）。
 * 确认后草稿锁定（后端 409），快照 version 递增。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Checkbox, Input, InputNumber, Popconfirm, Space, Table, Tag, Tree, Typography, message,
} from 'antd'
import { CheckOutlined, PlusOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import type { DataNode } from 'antd/es/tree'
import {
  confirmOutline, getAnalysis, getOutline, getProject, saveOutline,
  type AnalysisOut, type OutlineNodeData,
} from '../api'

interface TreeNodeData extends DataNode {
  nodeId: string
  targetWords: number
  scoringKeys: string[]
  children: TreeNodeData[]
}

function toDataNode(n: OutlineNodeData): TreeNodeData {
  return {
    key: n.id,
    nodeId: n.id,
    targetWords: n.target_words,
    scoringKeys: n.scoring_keys ?? [],
    title: `${n.id} ${n.title}`,
    children: (n.children ?? []).map(toDataNode),
  }
}

function fromDataNode(n: TreeNodeData): OutlineNodeData {
  const title = String(n.title).replace(/^\S+\s/, '')
  return {
    id: n.nodeId,
    title,
    target_words: n.targetWords,
    scoring_keys: n.scoringKeys,
    children: n.children.map(fromDataNode),
  }
}

export default function OutlinePage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const nav = useNavigate()
  const qc = useQueryClient()

  const { data: project } = useQuery({ queryKey: ['project', pid], queryFn: () => getProject(pid) })
  const { data: outline, isLoading } = useQuery({ queryKey: ['outline', pid], queryFn: () => getOutline(pid) })
  const { data: analysis } = useQuery<AnalysisOut>({ queryKey: ['analysis', pid], queryFn: () => getAnalysis(pid) })

  const [treeData, setTreeData] = useState<TreeNodeData[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const editable = project?.state === 'outline_pending'

  useEffect(() => {
    if (outline) setTreeData(outline.tree.nodes.map(toDataNode))
  }, [outline])

  const scoringMap = useMemo(() => {
    const m = new Map<string, { item: string; score: number; criteria: string }>()
    analysis?.scoring_items.forEach((s) =>
      m.set(s.item_key, { item: s.item, score: s.score, criteria: s.criteria_original })
    )
    return m
  }, [analysis])
  void scoringMap // 预留：章节气泡展示评分标准原文

  const techItems = analysis?.scoring_items.filter((s) => s.category === '技术') ?? []

  const findNode = (nodes: TreeNodeData[], key: string): TreeNodeData | null => {
    for (const n of nodes) {
      if (n.nodeId === key) return n
      const found = findNode(n.children, key)
      if (found) return found
    }
    return null
  }

  const updateNode = (key: string, patch: Partial<Pick<TreeNodeData, 'targetWords' | 'scoringKeys'>> & { title?: string }) => {
    const walk = (nodes: TreeNodeData[]): TreeNodeData[] =>
      nodes.map((n) => {
        if (n.nodeId === key) {
          const newTitle = patch.title !== undefined ? `${n.nodeId} ${patch.title}` : n.title
          return { ...n, ...patch, title: newTitle }
        }
        return { ...n, children: walk(n.children) }
      })
    setTreeData((prev) => walk(prev))
    setDirty(true)
  }

  // 防抖保存
  useEffect(() => {
    if (!dirty || !editable) return
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      try {
        await saveOutline(pid, { nodes: treeData.map(fromDataNode) })
        setDirty(false)
        qc.setQueryData(['outline', pid], (old: unknown) =>
          old ? { ...(old as object), tree: { nodes: treeData.map(fromDataNode) } } : old
        )
      } catch (e) {
        message.error(e instanceof Error ? e.message : '保存失败')
      }
    }, 1000)
    return () => clearTimeout(saveTimer.current)
  }, [treeData, dirty, editable, pid, qc])

  const confirmMut = useMutation({
    mutationFn: () => confirmOutline(pid),
    onSuccess: (d) => {
      message.success(`大纲已确认（v${d.version}），进入起草阶段`)
      qc.invalidateQueries({ queryKey: ['project', pid] })
      nav(`/projects/${pid}`)
    },
    onError: (e) => message.error(e instanceof Error ? e.message : '确认失败'),
  })

  const sel = selected ? findNode(treeData, selected) : null
  const totalWords = useMemo(() => {
    const sum = (nodes: TreeNodeData[]): number =>
      nodes.reduce((acc, n) => acc + (n.children.length ? 0 : n.targetWords) + sum(n.children), 0)
    return sum(treeData)
  }, [treeData])

  const coveredKeys = useMemo(() => {
    const s = new Set<string>()
    const walk = (nodes: TreeNodeData[]) => nodes.forEach((n) => { n.scoringKeys.forEach((k) => s.add(k)); walk(n.children) })
    walk(treeData)
    return s
  }, [treeData])
  const uncoveredTech = techItems.filter((t) => !coveredKeys.has(t.item_key))

  if (isLoading) return <Card loading />

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="大纲设计（唯一人工确认点）"
        extra={
          <Space>
            <Typography.Text type="secondary">全书目标 {totalWords.toLocaleString()} 字</Typography.Text>
            {dirty && <Tag color="orange">未保存…</Tag>}
            {editable ? (
              <Popconfirm
                title="确认大纲后进入起草，确认前请核对评分点挂接"
                onConfirm={() => confirmMut.mutate()}
                okText="确认大纲"
                cancelText="再想想"
              >
                <Button type="primary" icon={<CheckOutlined />} loading={confirmMut.isPending} disabled={dirty}>
                  确认大纲
                </Button>
              </Popconfirm>
            ) : (
              <Tag color={project?.state === 'outline_confirmed' ? 'cyan' : 'default'}>
                {project?.state === 'outline_confirmed' ? `已确认 v${project.outline_version}` : project?.state}
              </Tag>
            )}
          </Space>
        }
      >
        {uncoveredTech.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`${uncoveredTech.length} 个技术评分点未挂接章节：${uncoveredTech.map((t) => t.item_key).join('、')}`}
          />
        )}
        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Tree
              treeData={treeData}
              defaultExpandAll
              selectedKeys={selected ? [selected] : []}
              onSelect={(keys) => setSelected(keys[0] as string ?? null)}
            />
            {editable && (
              <Button
                size="small"
                icon={<PlusOutlined />}
                style={{ marginTop: 8 }}
                onClick={() => {
                  const newId = String(treeData.length + 1)
                  setTreeData([...treeData, { key: newId, nodeId: newId, title: `${newId} 新章节`, targetWords: 3000, scoringKeys: [], children: [] }])
                  setDirty(true)
                }}
              >
                加一级章
              </Button>
            )}
          </div>
          <Card size="small" title={sel ? `章节 ${sel.nodeId}` : '选中左侧章节编辑'} style={{ width: 420 }}>
            {sel ? (
              <Space direction="vertical" style={{ width: '100%' }}>
                <div>
                  <Typography.Text type="secondary">标题</Typography.Text>
                  <Input
                    value={String(sel.title).replace(/^\S+\s/, '')}
                    disabled={!editable}
                    onChange={(e) => updateNode(sel.nodeId, { title: e.target.value })}
                  />
                </div>
                <div>
                  <Typography.Text type="secondary">目标字数</Typography.Text>
                  <InputNumber
                    style={{ width: '100%' }}
                    min={0}
                    step={500}
                    value={sel.targetWords}
                    disabled={!editable}
                    onChange={(v) => updateNode(sel.nodeId, { targetWords: v ?? 0 })}
                  />
                </div>
                <div>
                  <Typography.Text type="secondary">挂接技术评分点</Typography.Text>
                  <Checkbox.Group
                    style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                    disabled={!editable}
                    value={sel.scoringKeys}
                    onChange={(vals) => updateNode(sel.nodeId, { scoringKeys: vals as string[] })}
                    options={techItems.map((t) => ({
                      label: `${t.item_key} ${t.item}（${t.score} 分）`,
                      value: t.item_key,
                    }))}
                  />
                </div>
              </Space>
            ) : (
              <Typography.Text type="secondary">点击章节查看/编辑标题、字数、评分点挂接</Typography.Text>
            )}
          </Card>
        </div>
      </Card>

      <Card title="评分点清单（解析结果）" size="small">
        <ScoringTable analysis={analysis} coveredKeys={coveredKeys} />
      </Card>
    </Space>
  )
}

function ScoringTable({ analysis, coveredKeys }: { analysis?: AnalysisOut; coveredKeys: Set<string> }) {
  return (
    <Table
      rowKey="item_key"
      size="small"
      pagination={false}
      dataSource={analysis?.scoring_items}
      columns={[
        { title: '编号', dataIndex: 'item_key', width: 70 },
        { title: '分卷', dataIndex: 'category', width: 80, render: (v) => <Tag color={v === '技术' ? 'blue' : 'default'}>{v}</Tag> },
        { title: '评分项', dataIndex: 'item', width: 160 },
        { title: '分值', dataIndex: 'score', width: 70 },
        { title: '评分标准原文', dataIndex: 'criteria_original', ellipsis: true },
        {
          title: '挂接',
          key: 'covered',
          width: 80,
          render: (_, r) => (r.category !== '技术' ? '—' : coveredKeys.has(r.item_key) ? <Tag color="success">已挂</Tag> : <Tag color="warning">未挂</Tag>),
        },
      ]}
    />
  )
}
