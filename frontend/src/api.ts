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

export const draftAllChapters = (id: number) =>
  request<{ dispatched: number }>(`/api/projects/${id}/chapters/draft-all`, { method: 'POST' })
export const listChapters = (id: number) => request<ChapterOut[]>(`/api/projects/${id}/chapters`)
export const getChapter = (id: number, chapterId: number) =>
  request<ChapterContentOut>(`/api/projects/${id}/chapters/${chapterId}`)
export const saveChapter = (id: number, chapterId: number, contentMd: string) =>
  request<ChapterContentOut>(`/api/projects/${id}/chapters/${chapterId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content_md: contentMd }),
  })
export const listChapterVersions = (id: number, chapterId: number) =>
  request<ChapterVersionOut[]>(`/api/projects/${id}/chapters/${chapterId}/versions`)

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
