# Bid-AgentMate

技术标书智能编写平台 —— 把 bid-skills（AI 编程助手 skill 家族）升级为独立软件产品。

**状态**：开发中。设计文档（v0.2 定稿，13 项决策）见 [DESIGN.md](DESIGN.md)。

**上游参考**：`git@github.com:HUAN2022A/bid-skills.git`（skill 版，核心脚本与设计理念来源）

## 一句话

输入招标文件（docx/pdf），输出逐点响应评分标准、有真实公司素材支撑、排版规范的技术文件 docx 终稿。

## 项目结构

```
backend/            FastAPI 后端
  app/
    api/            路由
    core/           配置、LLM 接入
    models/         SQLAlchemy 模型
    services/       业务逻辑
  scripts/          从 bid-skills 移植的核心脚本（extract/check/export）
frontend/           前端（Vue 3）
```

## 开发

```bash
# 后端
cd backend
../.venv/Scripts/pip install -r requirements.txt   # Windows
../.venv/bin/pip install -r requirements.txt       # Linux/macOS
uvicorn app.main:app --reload

# 前端（待建）
cd frontend
```

## 实施路径

- **阶段 1**（当前）：脚本层服务化——extract/check/export 包成 FastAPI 接口，验证"上传招标文件→自动解析→下载分析报告"最短闭环
- 阶段 2：LLM 编排层（评分点拆解、逐章起草）
- 阶段 3：协作与管理（多用户、权限、版本历史）
