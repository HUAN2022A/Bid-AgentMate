import { useQuery } from '@tanstack/react-query'
import { Button, Layout, Space, Typography } from 'antd'
import { LogoutOutlined } from '@ant-design/icons'
import { Navigate, Outlet, Route, Routes, useNavigate } from 'react-router-dom'
import { clearToken, getMe, getToken } from './api'
import LoginPage from './pages/LoginPage'
import ProjectListPage from './pages/ProjectListPage'
import ProjectDetailPage from './pages/ProjectDetailPage'
import OutlinePage from './pages/OutlinePage'
import ChaptersPage from './pages/ChaptersPage'
import ChapterEditorPage from './pages/ChapterEditorPage'
import DeliveryPage from './pages/DeliveryPage'

function Shell() {
  const nav = useNavigate()
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe })

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography.Title
          level={4}
          style={{ color: '#fff', margin: 0, cursor: 'pointer' }}
          onClick={() => nav('/')}
        >
          Bid-AgentMate
        </Typography.Title>
        <Space>
          <Typography.Text style={{ color: 'rgba(255,255,255,0.85)' }}>
            {me?.display_name || me?.username}
          </Typography.Text>
          <Button
            size="small"
            ghost
            icon={<LogoutOutlined />}
            onClick={() => {
              clearToken()
              nav('/login', { replace: true })
            }}
          >
            退出
          </Button>
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: 24, maxWidth: 1080, width: '100%', margin: '0 auto' }}>
        <Outlet />
      </Layout.Content>
    </Layout>
  )
}

/** 路由守卫：无 token 跳登录 */
function RequireAuth() {
  if (!getToken()) return <Navigate to="/login" replace />
  return <Shell />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<ProjectListPage />} />
        <Route path="/projects/:id" element={<ProjectDetailPage />} />
        <Route path="/projects/:id/outline" element={<OutlinePage />} />
        <Route path="/projects/:id/chapters" element={<ChaptersPage />} />
        <Route path="/projects/:id/chapters/:chapterId" element={<ChapterEditorPage />} />
        <Route path="/projects/:id/delivery" element={<DeliveryPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
