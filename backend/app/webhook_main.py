"""GitHub-only HTTP surface; never expose the development-identity API publicly."""

from fastapi import FastAPI

from app.api.github_webhook import router

app = FastAPI(title="DeployFlow GitHub webhook")
app.include_router(router)
