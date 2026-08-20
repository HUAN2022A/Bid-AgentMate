"""配置：.env + pydantic-settings 统一读取（Q29 定稿）。

LLM 配置嵌套预留 llm.parse.*/llm.draft.* 拆分（当前共用一组）。
环境变量优先覆盖 .env 文件。
"""
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 8192
    timeout_seconds: int = 300
    max_retries: int = 2  # 结构化输出校验失败后的喂错重试上限（Q26）


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_nested_delimiter="__"
    )

    # 数据库 / Redis
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/bidagentmate"
    redis_url: str = "redis://localhost:6379/0"

    # 文件存储（Q18/Q25：本地磁盘 + 抽象接口）
    data_root: str = "./data"

    # 认证（Q23：最简账号密码 + JWT）
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 60 * 24 * 7  # 私有化内网，7 天免频繁登录
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"  # 首次启动后应立即修改

    # 上传限制
    max_upload_mb: int = 200  # 招标 PDF 可达百兆级（金标准 92 页）

    # LLM（Q3/Q26）
    llm: LLMSettings = LLMSettings()

    # 阶段 1 演示模式：解析任务同步执行（不依赖 Redis/Celery），默认 false 走 Celery
    sync_tasks: bool = False


settings = Settings()
