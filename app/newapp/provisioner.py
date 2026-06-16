"""NewAppProvisioner — auto-config for scaffolded apps (spec §9.2, §9.3).

After Opus scaffolds a new app, this:
  * registers a PROJECT_<NAME>_* section in .env (placeholders for the bits
    Mario must fill: Railway IDs),
  * optionally creates the GitHub repo (Mario-authorized only),
  * generates a brief, actionable setup log (Railway / Auth0 / push steps).

It does NOT auto-push or auto-create Railway/Auth0 — those stay manual per the
spec, documented in the setup log.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ..integrations import GitHubClient

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "new-app"


def _is_python(stack: str) -> bool:
    s = (stack or "").lower()
    return not any(t in s for t in ("astro", "typescript", "node", "react"))


class NewAppProvisioner:
    def __init__(self, config, db, audit, github: Optional[GitHubClient] = None):
        self._config = config
        self._db = db
        self._audit = audit
        self._github = github or GitHubClient(config)

    # ------------------------------------------------------------------ #
    def _prog_projects(self) -> Path:
        base = self._config.get_raw("PROJECT_WEBSITE_REPO_PATH")
        return Path(base).parent if base else self._config.root

    def env_prefix(self, name: str) -> str:
        return "PROJECT_" + _slug(name).upper().replace("-", "_")

    def register_env_section(self, name: str, stack: str,
                             repo_slug: str = "") -> dict:
        slug = _slug(name)
        prefix = self.env_prefix(name)
        owner = self._config.get_raw("GITHUB_OWNER", "ZillaSoft-io")
        python = _is_python(stack)
        keys = {
            f"{prefix}_REPO": name,
            f"{prefix}_REPO_PATH": str(self._prog_projects() / slug),
            f"{prefix}_GITHUB_REPO": repo_slug or f"{owner}/{slug}",
            f"{prefix}_GITHUB_BRANCH": "main",
            f"{prefix}_FRAMEWORK": "python_fastapi" if python else "astro",
            f"{prefix}_BUILD_COMMAND": "pip install -e ." if python else "pnpm build",
            f"{prefix}_TEST_COMMAND": "pytest tests/ -v" if python else "pnpm test",
            f"{prefix}_DEPLOY_TRIGGER": "railway" if python else "github_actions_manual",
            f"{prefix}_RAILWAY_PROJECT_ID": "<FILL>",
            f"{prefix}_RAILWAY_SERVICE_ID": "<FILL>",
            f"{prefix}_HEALTH_CHECK_URL": f"https://api.{slug}.app/health",
            f"{prefix}_HEALTH_CHECK_FORMAT": "json",
            f"{prefix}_HEALTH_CHECK_JSON_KEY": "status",
            f"{prefix}_HEALTH_CHECK_EXPECTED_VALUE": "ok",
            f"{prefix}_HEALTH_CHECK_EXPECTED_STATUS": "200",
        }
        for k, v in keys.items():
            self._config.set(k, v, actor="agent")  # project config — writable
        logger.info("Registered %d .env keys for new app '%s'.", len(keys), name)
        return keys

    def create_repo(self, name: str, *, private: bool = True,
                    description: str = "") -> dict:
        return self._github.create_repo(_slug(name), private=private,
                                        description=description)

    def generate_setup_log(self, name: str, stack: str, repo_slug: str,
                           files: Optional[list] = None) -> str:
        slug = _slug(name)
        python = _is_python(stack)
        prefix = self.env_prefix(name)
        files_block = "\n".join(f"    {f}" for f in (files or [])) or \
            "    (see the scaffold directory)"
        deploy = ("Railway (Python/FastAPI)" if python
                  else "GitHub Actions + S3/CloudFront (Astro)")
        return (
            f"Created new app: {name} ({'Python + FastAPI' if python else 'Astro + TypeScript'})\n\n"
            f"Project structure:\n{files_block}\n\n"
            f"Deployment target: {deploy}\n\n"
            "Next steps to complete setup:\n"
            f"  1. Review the scaffold and push: git push -u origin main "
            f"(repo: {repo_slug})\n"
            + ("  2. Create a Railway project: https://railway.app/new\n"
               f"  3. Update .env: {prefix}_RAILWAY_PROJECT_ID and "
               f"{prefix}_RAILWAY_SERVICE_ID\n"
               if python else
               "  2. Confirm the GitHub Actions deploy workflow and S3/CloudFront targets\n")
            + "  4. If using Auth0/Stripe/Sentry, add their credentials to .env "
              "(Mario only, via the UI)\n"
            f"  5. Verify the health check once deployed: "
            f"curl https://api.{slug}.app/health\n"
        )

    # ------------------------------------------------------------------ #
    def provision(self, session: dict, *, create_repo: bool = False,
                  files: Optional[list] = None) -> dict:
        sid = session["id"]
        ctx = session.get("haiku_context") or {}
        name = ctx.get("app_name") or f"new-app-{sid[:8]}"
        stack = ctx.get("recommended_stack") or ""

        repo_slug = ""
        repo_info = None
        if create_repo and self._github.configured():
            repo_info = self.create_repo(name, description=ctx.get("summary", ""))
            repo_slug = repo_info["slug"]

        env_keys = self.register_env_section(name, stack, repo_slug=repo_slug)
        setup_log = self.generate_setup_log(
            name, stack, repo_slug or env_keys[self.env_prefix(name) + "_GITHUB_REPO"],
            files=files)

        self._db.update_session(sid, setup_log=setup_log)
        self._audit.update(sid, session.get("project"), {
            "setup_log": setup_log,
            "new_app": {"name": name, "stack": stack,
                        "repo": repo_slug, "env_keys": list(env_keys)},
        })
        logger.info("Provisioned new app '%s' (repo=%s).", name, repo_slug or "—")
        return {"name": name, "repo": repo_info, "env_keys": env_keys,
                "setup_log": setup_log}
