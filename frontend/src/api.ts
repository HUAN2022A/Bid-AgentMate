/** API 客户端：fetch 薄封装 + JWT 存取。401 统一跳登录。 */

const TOKEN_KEY = 'bam_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const resp = await fetch(path, { ...init, headers })
  if (resp.status === 401) {
    clearToken()
    window.location.href = '/login'
    throw new ApiError(401, '登录已失效')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = body.detail || detail
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

// ---- 类型（与后端 schema 对齐）----

export interface UserOut {
  id: number
  username: string
  display_name: string
}

export interface ProjectOut {
  id: number
  name: string
  tender_no: string
  state: string
  parse_error: string
  outline_version: number
  created_at: string
}

export interface TenderFileOut {
  id: number
  role: string
  file_type: string
  original_name: string
  size: number
  extract_stats: string
  extracted: boolean
}

// ---- 接口 ----

export async function login(username: string, password: string): Promise<void> {
  const form = new URLSearchParams()
  form.set('username', username)
  form.set('password', password)
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new ApiError(resp.status, body.detail || '登录失败')
  }
  const data = await resp.json()
  setToken(data.access_token)
}

export const getMe = () => request<UserOut>('/api/auth/me')
export const listProjects = () => request<ProjectOut[]>('/api/projects')
export const createProject = (name: string, tenderNo: string) =>
  request<ProjectOut>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, tender_no: tenderNo }),
  })
export const getProject = (id: number) => request<ProjectOut>(`/api/projects/${id}`)
export const listTenderFiles = (id: number) =>
  request<TenderFileOut[]>(`/api/projects/${id}/tender`)

// ---- 阶段 2：解析结果 + 大纲 ----

export interface ScoringItemOut {
  item_key: string
  category: string
  item: string
  score: number
  criteria_original: string
  location: string
  response_hint: string
}

export interface TechRequirementOut {
  req_key: string
  star: boolean
  requirement_original: string
  location: string
}

export interface AnalysisOut {
  scoring_items: ScoringItemOut[]
  tech_requirements: TechRequirementOut[]
}

export interface OutlineNodeData {
  id: string
  title: string
  target_words: number
  scoring_keys: string[]
  children: OutlineNodeData[]
}

export interface OutlineDraftOut {
  tree: { nodes: OutlineNodeData[] }
  ai_raw_tree: { nodes: OutlineNodeData[] }
  updated_at: string
}

export const getAnalysis = (id: number) => request<AnalysisOut>(`/api/projects/${id}/analysis`)
export const getOutline = (id: number) => request<OutlineDraftOut>(`/api/projects/${id}/outline`)
export const saveOutline = (id: number, tree: { nodes: OutlineNodeData[] }) =>
  request<OutlineDraftOut>(`/api/projects/${id}/outline`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tree }),
  })
export const confirmOutline = (id: number) =>
  request<{ version: number; state: string }>(`/api/projects/${id}/outline/confirm`, { method: 'POST' })

// ---- 阶段 2：章节起草 ----

export interface ChapterOut {
  id: number
  chapter_key: string
  title: string
  target_words: number
  scoring_keys: string
  state: string
  draft_error: string
  needs_review: boolean
  word_count: number
}

export interface ChapterContentOut {
  id: number
  chapter_key: string
  title: string
  state: string
  scoring_keys: string
  content_md: string
  version_no: number
  word_count: number
  target_words: number
}

export interface ChapterVersionOut {
  version_no: number
  source: string
  word_count: number
  created_at: string
}

/** 保存时声明版本来源（Q24）：应用 Copilot 结果后未再手改 → ai_paragraph，否则 human */
export type SaveSourceHint = 'human' | 'ai_paragraph'

export const draftAllChapters = (id: number) =>
  request<{ dispatched: number }>(`/api/projects/${id}/chapters/draft-all`, { method: 'POST' })
export const listChapters = (id: number) => request<ChapterOut[]>(`/api/projects/${id}/chapters`)
export const getChapter = (id: number, chapterId: number) =>
  request<ChapterContentOut>(`/api/projects/${id}/chapters/${chapterId}`)
export const saveChapter = (id: number, chapterId: number, contentMd: string, sourceHint: SaveSourceHint = 'human') =>
  request<ChapterContentOut>(`/api/projects/${id}/chapters/${chapterId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content_md: contentMd, source_hint: sourceHint }),
  })
export const listChapterVersions = (id: number, chapterId: number) =>
  request<ChapterVersionOut[]>(`/api/projects/${id}/chapters/${chapterId}/versions`)

// ---- 章节内 Copilot（Q8 段落级动作）----

export type CopilotActionName = 'rewrite' | 'expand' | 'compress' | 'align_scoring' | 'tabulate' | 'star_response'

export const COPILOT_ACTION_LABEL: Record<CopilotActionName, string> = {
  rewrite: '重写',
  expand: '扩写',
  compress: '压缩',
  align_scoring: '对齐评分点',
  tabulate: '表格化',
  star_response: '★条款响应',
}

export interface CopilotRequest {
  action: CopilotActionName
  selection_md?: string
  instruction?: string
  scoring_key?: string
  requirement_key?: string
  prev_md?: string
  next_md?: string
}

export interface CopilotResponse {
  request_id: number
  content_md: string
  warnings: string[]
  context_refs: { scoring_keys?: string[]; material_ids?: number[]; requirement_key?: string }
  /** 流式 done 事件携带的诊断耗时（非流式端点无此字段） */
  latency_ms?: number
}

/** 同步调用；signal 供取消（后端结果成为未应用的孤儿行 = 放弃信号） */
export const runCopilot = (id: number, chapterId: number, body: CopilotRequest, signal?: AbortSignal) =>
  request<CopilotResponse>(`/api/projects/${id}/chapters/${chapterId}/copilot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

/** SSE 单个事件块（一个空行分隔的若干行）的解析结果 */
interface SseEvent {
  event: string
  data: string
}

/** 解析一个 SSE 事件块：event:/data: 前缀取值（多行 data 按 SSE 规范以 \n join），冒号开头为注释忽略 */
function parseSseBlock(block: string): SseEvent | null {
  let event = ''
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).startsWith(' ') ? line.slice(6) : line.slice(5))
  }
  if (!event && dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/** 流式 Copilot：fetch + ReadableStream 手解 SSE（EventSource 带不了 Authorization 头）。
 *
 * - 事件协议：delta{text} 可多次 → done{request_id,content_md,warnings,context_refs,latency_ms} | error{message} 互斥
 * - done 的 content_md 是服务端权威全文，调用方应以此覆盖本地累积
 * - 开流前的 404/422 是普通 JSON 错误体（走 ApiError，404 供调用方降级非流式）
 * - 流读尽仍无 done → 连接中断；abort 时 reader 抛 AbortError 向上传播
 */
export async function streamCopilot(
  id: number,
  chapterId: number,
  body: CopilotRequest,
  handlers: { onDelta: (text: string) => void },
  signal?: AbortSignal,
): Promise<CopilotResponse> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const resp = await fetch(`/api/projects/${id}/chapters/${chapterId}/copilot/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (resp.status === 401) {
    clearToken()
    window.location.href = '/login'
    throw new ApiError(401, '登录已失效')
  }
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const err = await resp.json()
      detail = err.detail || detail
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(resp.status, detail)
  }
  if (!resp.body) throw new ApiError(0, '当前浏览器不支持流式响应')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let donePayload: CopilotResponse | null = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    for (;;) {
      const sep = buffer.indexOf('\n\n')
      if (sep < 0) break
      const ev = parseSseBlock(buffer.slice(0, sep))
      buffer = buffer.slice(sep + 2)
      if (!ev) continue
      if (ev.event === 'delta') {
        handlers.onDelta((JSON.parse(ev.data) as { text: string }).text)
      } else if (ev.event === 'done') {
        donePayload = JSON.parse(ev.data) as CopilotResponse
      } else if (ev.event === 'error') {
        throw new Error((JSON.parse(ev.data) as { message: string }).message)
      }
    }
  }
  if (!donePayload) throw new Error('连接中断，请重试')
  return donePayload
}
export const markCopilotApplied = (id: number, actionId: number) =>
  request<{ applied: boolean }>(`/api/projects/${id}/copilot-actions/${actionId}/applied`, { method: 'PATCH' })

export interface CopilotStatRow {
  key: string
  total: number
  applied: number
  apply_rate: number
  avg_latency_ms: number
  avg_output_chars: number
}

export interface CopilotStatsOut {
  total: number
  applied: number
  apply_rate: number
  by_action: CopilotStatRow[]
  by_model: CopilotStatRow[]
  top_instructions: { instruction: string; count: number }[]
}

export const getCopilotStats = (projectId?: number) =>
  request<CopilotStatsOut>(`/api/copilot-stats${projectId ? `?project_id=${projectId}` : ''}`)

// ---- 交付：自查 + 导出 ----

export interface CheckSummaryOut {
  report_path: string
  tech_items: number
  covered: number
  star_reqs: number
  star_hit: number
  pending_gaps: number
  price_hits: number
}

export interface ExportSummaryOut {
  export_path: string
  chapters: number
  total_words: number
  pending_gaps: number
  exported_at: string
}

export const runCheck = (id: number) =>
  request<CheckSummaryOut>(`/api/projects/${id}/check`, { method: 'POST' })
export const runExport = (id: number) =>
  request<ExportSummaryOut>(`/api/projects/${id}/export`, { method: 'POST' })

export interface ExportPreviewOut {
  project_name: string
  tender_no: string
  chapters: { key: string; title: string; words: number; pending: number }[]
  total_words: number
  pending_gaps: number
  style_notes: string[]
}

export const getExportPreview = (id: number) =>
  request<ExportPreviewOut>(`/api/projects/${id}/export/preview`)

// ---- 覆盖热力图：评分点 × 章节覆盖矩阵（实时计算，只读） ----

export type CoverageStatus = 'covered' | 'partial' | 'none'

export interface CoverageChapterOut {
  chapter_key: string
  title: string
  has_content: boolean
  word_count: number
}

export interface CoverageCellOut {
  chapter_key: string
  status: CoverageStatus
  hit_keywords: string[]
  evidence: string
}

export interface CoverageItemOut {
  item_key: string
  item: string
  category: string
  score: number
  criteria_brief: string
  linked_chapters: string[]
  row_status: CoverageStatus
  cells: CoverageCellOut[]
}

export interface CoverageSummaryOut {
  total: number
  covered: number
  partial: number
  none: number
}

export interface CoverageOut {
  chapters: CoverageChapterOut[]
  items: CoverageItemOut[]
  summary: CoverageSummaryOut
}

export const getCoverage = (id: number) => request<CoverageOut>(`/api/projects/${id}/coverage`)

export async function downloadFile(id: number, kind: 'check/report' | 'export/docx', filename: string): Promise<void> {
  const token = getToken()
  const resp = await fetch(`/api/projects/${id}/${kind}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new ApiError(resp.status, '下载失败')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// ---- 素材库 ----

export interface MaterialOut {
  id: number
  type: string
  name: string
  summary: string
  qual_extra: Record<string, unknown>
  tags: string
  source: string
  updated_at: string
}

export interface IngestResultOut {
  stats: Record<string, number>
  gaps: string[]
  source: string
}

export const listMaterials = (type?: string, q?: string) => {
  const params = new URLSearchParams()
  if (type) params.set('type', type)
  if (q) params.set('q', q)
  const qs = params.toString()
  return request<MaterialOut[]>(`/api/materials${qs ? '?' + qs : ''}`)
}
export const createMaterial = (body: Partial<MaterialOut>) =>
  request<MaterialOut>('/api/materials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
export const updateMaterial = (id: number, body: Partial<MaterialOut>) =>
  request<MaterialOut>(`/api/materials/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
export const deleteMaterial = (id: number) =>
  request<{ deleted: number }>(`/api/materials/${id}`, { method: 'DELETE' })

export async function ingestMaterial(file: File): Promise<IngestResultOut> {
  const form = new FormData()
  form.append('file', file)
  return request<IngestResultOut>('/api/materials/ingest', { method: 'POST', body: form })
}

export async function uploadTender(id: number, file: File, role = 'main'): Promise<TenderFileOut> {
  const form = new FormData()
  form.append('file', file)
  return request<TenderFileOut>(`/api/projects/${id}/tender?role=${role}`, { method: 'POST', body: form })
}

export const triggerParse = (id: number) =>
  request<ProjectOut>(`/api/projects/${id}/parse`, { method: 'POST' })

/** 下载提取全文：带 JWT 头拉 blob 再触发浏览器下载（直接开窗带不了 Authorization 头） */
export async function downloadExtracted(id: number): Promise<void> {
  const token = getToken()
  const resp = await fetch(`/api/projects/${id}/tender/extracted`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new ApiError(resp.status, '下载失败')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = '招标文件提取全文.txt'
  a.click()
  URL.revokeObjectURL(url)
}

/** 项目状态中文标签 + 颜色（AntD Tag/Badge 用） */
export const STATE_META: Record<string, { label: string; color: string }> = {
  created: { label: '已创建', color: 'default' },
  parsing: { label: '解析中', color: 'processing' },
  parse_failed: { label: '解析失败', color: 'error' },
  outline_pending: { label: '大纲待确认', color: 'warning' },
  outline_confirmed: { label: '大纲已确认', color: 'cyan' },
  drafting: { label: '起草中', color: 'processing' },
  draft_done: { label: '起草完成', color: 'cyan' },
  checking: { label: '自查中', color: 'processing' },
  exported: { label: '已导出', color: 'success' },
}

/** 章节状态中文标签 + 颜色（章节页与驾驶舱共用一份映射） */
export const CHAPTER_STATE_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待起草', color: 'default' },
  drafting: { label: '起草中', color: 'processing' },
  draft_done: { label: '起草完成', color: 'cyan' },
  draft_failed: { label: '起草失败', color: 'error' },
  edited: { label: '已编辑', color: 'success' },
}
