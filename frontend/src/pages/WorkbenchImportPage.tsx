/** 标书工作台·导入页：三文档上传 → 解析进度 → 投标目录树编辑确认 → 就绪导航。
 *
 * 状态机：created →（上传 main/spec/bid + 开始解析）wb_parsing → wb_outline_pending（树编辑：
 * 改标题/升降级/删节点，随改随存防抖 1s）→ wb_ready。wb_parse_failed 可重传/重试。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Empty, Input, Popconfirm, Space, Spin, Table, Tag, Tree, Typography, Upload, message,
} from 'antd'
import {
  ArrowDownOutlined, ArrowUpOutlined, CheckOutlined, DeleteOutlined,
  FileTextOutlined, FundProjectionScreenOutlined, SafetyOutlined, UploadOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import type { DataNode } from 'antd/es/tree'
import {
  confirmBidOutline, downloadFile, exportWorkbench, getAnalysis, getBidOutline, getProject,
  listChapters, listTenderFiles, saveBidOutline, triggerParse, uploadTender, type BidOutlineNode,
} from '../api'
import WorkbenchNav from '../components/workbench/WorkbenchNav'

type WbRole = 'main' | 'spec' | 'bid'

const WB_ROLE_META: Record<WbRole, { label: string; color: string; hint: string; accept: string }> = {
  main: { label: '招标文件', color: 'blue', hint: '必传 · 评分依据与废标条款来源', accept: '.pdf,.docx,.doc' },
  spec: { label: '技术规范书', color: 'purple', hint: '选传 · ★技术参数主来源', accept: '.pdf,.docx,.doc' },
  bid: { label: '投标文件', color: 'geekblue', hint: '必传 · 被润色/检查/评分的对象（支持 .doc）', accept: '.pdf,.docx,.doc' },
}

// ---- 树工具：antd 展示结构与 BidOutlineNode 互转 + 结构操作后重编号 ----

interface WbTreeNode extends DataNode {
  nodeId: string
  raw: BidOutlineNode
  children: WbTreeNode[]
}

function toDisplay(n: BidOutlineNode): WbTreeNode {
  const words = (n.content_md ?? '').length
  return {
    key: n.id,
    nodeId: n.id,
    raw: n,
    title: words ? `${n.id} ${n.title}（${words} 字）` : `${n.id} ${n.title}`,
    children: (n.children ?? []).map(toDisplay),
  }
}

/** 结构变更后按树位置重编号（1、1.1、1.1.1），保持 chapter_key 稳定语义 */
function renumber(nodes: BidOutlineNode[], prefix = ''): BidOutlineNode[] {
  return nodes.map((n, i) => {
    const id = prefix ? `${prefix}.${i + 1}` : String(i + 1)
    return { ...n, id, children: renumber(n.children ?? [], id) }
  })
}

function findAndMap(
  nodes: BidOutlineNode[], key: string, fn: (n: BidOutlineNode) => BidOutlineNode | null,
): BidOutlineNode[] {
  const out: BidOutlineNode[] = []
  for (const n of nodes) {
    if (n.id === key) {
      const r = fn(n)
      if (r) out.push(r)
    } else {
      out.push({ ...n, children: findAndMap(n.children ?? [], key, fn) })
    }
  }
  return out
}

function findNode(nodes: BidOutlineNode[], key: string): BidOutlineNode | null {
  for (const n of nodes) {
    if (n.id === key) return n
    const f = findNode(n.children ?? [], key)
    if (f) return f
  }
  return null
}

function countLeaves(nodes: BidOutlineNode[]): number {
  return nodes.reduce((acc, n) => acc + (n.children?.length ? 0 : 1) + countLeaves(n.children ?? []), 0)
}
function sumWords(nodes: BidOutlineNode[]): number {
  return nodes.reduce((acc, n) => acc + (n.content_md?.length ?? 0) + sumWords(n.children ?? []), 0)
}

export default function WorkbenchImportPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const qc = useQueryClient()
  const nav = useNavigate()

  const { data: project } = useQuery({
    queryKey: ['project', pid],
    queryFn: () => getProject(pid),
    refetchInterval: (q) => (q.state.data?.state === 'wb_parsing' ? 3000 : false),
  })
  const { data: tenders } = useQuery({ queryKey: ['tenders', pid], queryFn: () => listTenderFiles(pid) })
  const { data: bidOutline } = useQuery({
    queryKey: ['bid-outline', pid],
    queryFn: () => getBidOutline(pid),
    enabled: project?.state === 'wb_outline_pending' || project?.state === 'wb_ready',
  })
  const { data: analysis } = useQuery({
    queryKey: ['analysis', pid],
    queryFn: () => getAnalysis(pid),
    enabled: project?.state === 'wb_outline_pending' || project?.state === 'wb_ready',
  })
  const { data: chapters } = useQuery({
    queryKey: ['chapters', pid],
    queryFn: () => listChapters(pid),
    enabled: project?.state === 'wb_ready',
  })

  const state = project?.state ?? ''
  const canUpload = state === 'created' || state === 'wb_parse_failed'
  const hasMain = !!tenders?.some((t) => t.role === 'main')
  const hasBid = !!tenders?.some((t) => t.role === 'bid')

  const doUpload = async (file: File, role: WbRole) => {
    try {
      await uploadTender(pid, file, role)
      message.success(`${WB_ROLE_META[role].label}已上传`)
      qc.invalidateQueries({ queryKey: ['tenders', pid] })
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败')
    }
  }

  const doParse = async () => {
    try {
      await triggerParse(pid)
      message.success('解析已开始')
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '解析启动失败')
    }
  }

  // ---- 目录树编辑（wb_outline_pending） ----
  const [treeNodes, setTreeNodes] = useState<BidOutlineNode[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (bidOutline && state === 'wb_outline_pending') setTreeNodes(bidOutline.tree.nodes)
  }, [bidOutline, state])

  const applyChange = (nodes: BidOutlineNode[]) => {
    setTreeNodes(renumber(nodes))
    setDirty(true)
  }

  const updateTitle = (key: string, title: string) => {
    setTreeNodes((prev) => findAndMap(prev, key, (n) => ({ ...n, title })))
    setDirty(true)
  }

  /** 降级：成为上一个兄弟节点的最后一个子节点（首个兄弟不可降） */
  const demote = (key: string) => {
    const walk = (nodes: BidOutlineNode[]): BidOutlineNode[] => {
      const idx = nodes.findIndex((n) => n.id === key)
      if (idx > 0) {
        const node = nodes[idx]
        const prev = { ...nodes[idx - 1], children: [...(nodes[idx - 1].children ?? [])] }
        prev.children.push(node)
        return [...nodes.slice(0, idx - 1), prev, ...nodes.slice(idx + 1)]
      }
      if (idx === 0) return nodes
      return nodes.map((n) => ({ ...n, children: walk(n.children ?? []) }))
    }
    applyChange(walk(treeNodes))
  }

  /** 升级：提出到父节点的下一个兄弟位置（顶层节点不可升） */
  const promote = (key: string) => {
    const walk = (nodes: BidOutlineNode[]): BidOutlineNode[] => {
      if (nodes.some((n) => n.id === key)) return nodes
      return nodes.flatMap((n) => {
        const kids = n.children ?? []
        const hit = kids.find((k) => k.id === key)
        if (hit) {
          return [{ ...n, children: kids.filter((k) => k.id !== key) }, hit]
        }
        return [{ ...n, children: walk(kids) }]
      })
    }
    applyChange(walk(treeNodes))
  }

  const removeNode = (key: string) => applyChange(findAndMap(treeNodes, key, () => null))

  // 防抖保存
  useEffect(() => {
    if (!dirty || state !== 'wb_outline_pending') return
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      try {
        await saveBidOutline(pid, treeNodes)
        setDirty(false)
        qc.setQueryData(['bid-outline', pid], (old: unknown) =>
          old ? { ...(old as object), tree: { nodes: treeNodes } } : old
        )
      } catch (e) {
        message.error(e instanceof Error ? e.message : '保存失败')
      }
    }, 1000)
    return () => clearTimeout(saveTimer.current)
  }, [treeNodes, dirty, state, pid, qc])

  const doConfirm = async () => {
    setConfirming(true)
    try {
      if (dirty) {
        clearTimeout(saveTimer.current)
        await saveBidOutline(pid, treeNodes)
        setDirty(false)
      }
      const r = await confirmBidOutline(pid)
      message.success(`投标目录已确认（v${r.version}，${r.chapters} 章导入）`)
      qc.invalidateQueries({ queryKey: ['project', pid] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '确认失败')
    } finally {
      setConfirming(false)
    }
  }

  const sel = selected ? findNode(treeNodes, selected) : null
  const treeData = useMemo(() => treeNodes.map(toDisplay), [treeNodes])

  if (!project) return <Card loading />

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <WorkbenchNav pid={pid} active="import" />
      <Card
        title={
          <Space>
            项目导入
            <Tag color={WB_ROLE_META.main.color}>{project.name}</Tag>
          </Space>
        }
        extra={
          canUpload ? (
            <Popconfirm
              title="开始解析"
              description="将解析招标文件（评分点/废标条款）并提取投标文件章节树，大文件可能需要几分钟"
              onConfirm={doParse}
              okText="开始"
              cancelText="取消"
            >
              <Button type="primary" icon={<FileTextOutlined />} disabled={!hasMain || !hasBid}>
                开始解析
              </Button>
            </Popconfirm>
          ) : state === 'wb_parsing' ? (
            <Tag color="processing">解析中…</Tag>
          ) : state === 'wb_ready' ? (
            <Tag color="success">工作台就绪</Tag>
          ) : null
        }
      >
        {state === 'wb_parsing' && (
          <Alert
            type="info" showIcon
            icon={<Spin />}
            message="正在解析：招标文件 LLM 拆解 + 投标文件结构提取（.doc 需先无头转换，116MB 级文件约需 1-2 分钟）"
            style={{ marginBottom: 16 }}
          />
        )}
        {project.parse_error && (
          <Alert
            type={state === 'wb_parse_failed' ? 'error' : 'warning'}
            showIcon
            message={state === 'wb_parse_failed' ? '解析失败（可重新上传文件后再试）' : '解析提示'}
            description={project.parse_error}
            style={{ marginBottom: 16 }}
          />
        )}

        {canUpload || state === 'wb_parsing' ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div style={{ display: 'flex', gap: 12 }}>
              {(['main', 'spec', 'bid'] as WbRole[]).map((role) => {
                const meta = WB_ROLE_META[role]
                const got = tenders?.filter((t) => t.role === role) ?? []
                return (
                  <Card
                    key={role} size="small" style={{ flex: 1 }}
                    title={<Tag color={meta.color}>{meta.label}</Tag>}
                    extra={got.length ? <Tag color="success">已上传 {got.length}</Tag> : null}
                  >
                    <Upload.Dragger
                      accept={meta.accept}
                      maxCount={1}
                      showUploadList={false}
                      disabled={!canUpload}
                      customRequest={({ file }) => doUpload(file as File, role)}
                    >
                      <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                      <p className="ant-upload-text">{canUpload ? '点击或拖拽上传' : '解析中，暂不可传'}</p>
                      <p className="ant-upload-hint">{meta.hint}</p>
                    </Upload.Dragger>
                  </Card>
                )
              })}
            </div>
            {!hasMain && <Alert type="info" showIcon message="请先上传招标文件（评分点与废标条款的来源）" />}
            {hasMain && !hasBid && <Alert type="warning" showIcon message="请上传投标文件（润色/检查/评分的对象）" />}
          </Space>
        ) : state === 'wb_outline_pending' ? (
          <div style={{ display: 'flex', gap: 16 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Space style={{ marginBottom: 8 }}>
                <Typography.Text type="secondary">
                  叶章 {countLeaves(treeNodes)} · 全书 {sumWords(treeNodes).toLocaleString()} 字
                </Typography.Text>
                {dirty && <Tag color="orange">未保存…</Tag>}
                <Popconfirm
                  title="确认投标目录"
                  description="确认后按当前树物化章节并进入工作台；此后修改章节请到润色页"
                  onConfirm={doConfirm}
                  okText="确认目录"
                  cancelText="再看看"
                >
                  <Button type="primary" icon={<CheckOutlined />} loading={confirming} disabled={dirty}>
                    确认目录
                  </Button>
                </Popconfirm>
              </Space>
              <Tree
                treeData={treeData}
                defaultExpandAll
                blockNode
                selectedKeys={selected ? [selected] : []}
                onSelect={(keys) => setSelected((keys[0] as string) ?? null)}
              />
            </div>
            <Card size="small" title={sel ? `章节 ${sel.id}` : '选中左侧章节编辑'} style={{ width: 440 }}>
              {sel ? (
                <Space direction="vertical" style={{ width: '100%' }} size={12}>
                  <div>
                    <Typography.Text type="secondary">标题（随改随存）</Typography.Text>
                    <Input value={sel.title} onChange={(e) => updateTitle(sel.id, e.target.value)} />
                  </div>
                  <Space>
                    <Button size="small" icon={<ArrowUpOutlined />} onClick={() => promote(sel.id)}>
                      升一级
                    </Button>
                    <Button size="small" icon={<ArrowDownOutlined />} onClick={() => demote(sel.id)}>
                      降一级
                    </Button>
                    <Popconfirm title="删除该章节及其子树？" onConfirm={() => { removeNode(sel.id); setSelected(null) }} okText="删除" cancelText="取消">
                      <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                    </Popconfirm>
                  </Space>
                  {sel.children?.length ? (
                    <Typography.Text type="secondary">非叶章：正文（如有）确认时并入首个叶章。子章 {sel.children.length} 个。</Typography.Text>
                  ) : (
                    <div>
                      <Typography.Text type="secondary">导入正文预览（{sel.content_md?.length ?? 0} 字）</Typography.Text>
                      <Typography.Paragraph
                        style={{ maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', background: '#fafafa', padding: 8 }}
                        ellipsis={{ rows: 10, expandable: true, symbol: '展开全文' }}
                      >
                        {sel.content_md || '（无正文）'}
                      </Typography.Paragraph>
                    </div>
                  )}
                </Space>
              ) : (
                <Typography.Text type="secondary">
                  点击章节编辑标题、调整层级（升/降一级）、删除；正文在确认后可在润色页修改
                </Typography.Text>
              )}
            </Card>
          </div>
        ) : state === 'wb_ready' ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space size="large">
              <Typography.Text>
                投标文件已导入：<Typography.Text strong>{chapters?.length ?? '—'}</Typography.Text> 章 ·
                共 <Typography.Text strong>{(chapters?.reduce((a, c) => a + c.word_count, 0) ?? 0).toLocaleString()}</Typography.Text> 字
              </Typography.Text>
              <Typography.Text type="secondary">
                评分点 {analysis?.scoring_items.length ?? '—'} 个（技术{' '}
                {analysis?.scoring_items.filter((s) => s.category === '技术').length ?? '—'}）
              </Typography.Text>
            </Space>
            <Space>
              <Button type="primary" icon={<FileTextOutlined />} onClick={() => nav(`/projects/${pid}/wb-polish`)}>
                标书润色
              </Button>
              <Button icon={<SafetyOutlined />} onClick={() => nav(`/projects/${pid}/wb-check`)}>
                标书检查
              </Button>
              <Button icon={<FundProjectionScreenOutlined />} onClick={() => nav(`/projects/${pid}/wb-scoring`)}>
                技术评分
              </Button>
              <Button
                icon={<ArrowDownOutlined />}
                onClick={async () => {
                  try {
                    const r = await exportWorkbench(pid)
                    message.success(`docx 已生成（${r.chapters} 章 / ${r.total_words.toLocaleString()} 字），开始下载`)
                    await downloadFile(pid, 'wb/export/docx', '投标文件-工作台版.docx')
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : '导出失败')
                  }
                }}
              >
                导出投标文件
              </Button>
            </Space>
          </Space>
        ) : (
          <Empty description={`状态 ${state}`} />
        )}
      </Card>

      {(state === 'wb_outline_pending' || state === 'wb_ready') && analysis && (
        <Card title="评分点预览（招标解析结果）" size="small">
          <Table
            rowKey="item_key"
            size="small"
            pagination={false}
            dataSource={analysis.scoring_items}
            columns={[
              { title: '编号', dataIndex: 'item_key', width: 70 },
              { title: '分卷', dataIndex: 'category', width: 80, render: (v) => <Tag color={v === '技术' ? 'blue' : 'default'}>{v}</Tag> },
              { title: '评分项', dataIndex: 'item', width: 180, ellipsis: true },
              { title: '分值', dataIndex: 'score', width: 70 },
              { title: '评分标准原文', dataIndex: 'criteria_original', ellipsis: true },
            ]}
          />
          {analysis.tech_requirements.length > 0 && (
            <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
              ★/▲ 技术要求 {analysis.tech_requirements.filter((t) => t.star).length} 条（硬指标，检查页逐条核对）
            </Typography.Text>
          )}
        </Card>
      )}
    </Space>
  )
}
