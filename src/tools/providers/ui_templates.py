"""
UIBuilder Templates Provider
============================

Template-based UI scaffolding provider for deterministic project generation.
Provides a baseline UIBuilder capability without requiring Antigravity.

Templates:
- nextjs_shadcn_dashboard: Next.js + shadcn/ui admin dashboard
- fastapi_service: FastAPI microservice with async endpoints
- streamlit_dashboard: Streamlit data dashboard
- flask_api: Flask REST API with SQLAlchemy

Prompt 7 — UIBuilder Templates
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.tools.contracts import (
    BaseProvider,
    Capability,
    ProviderHealth,
    ProviderState,
    ScaffoldResult,
    UIBuilderMode,
    UIBuilderCapability,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class TemplateConfig:
    """Configuration for TemplateScaffolder."""

    # Template directories
    template_base_path: Optional[str] = None  # Custom template location

    # Build verification
    verify_builds: bool = True
    build_timeout_s: int = 300

    # Default values
    default_author: str = "Lancelot"
    default_license: str = "MIT"


# =============================================================================
# Template Definitions
# =============================================================================


TEMPLATES: Dict[str, Dict[str, Any]] = {
    "nextjs_shadcn_dashboard": {
        "id": "nextjs_shadcn_dashboard",
        "name": "Next.js + shadcn/ui Dashboard",
        "description": "Admin dashboard with Next.js 14, shadcn/ui components, and Tailwind CSS",
        "framework": "nextjs",
        "ui_library": "shadcn",
        "features": ["app-router", "dark-mode", "responsive", "typescript"],
        "files": {
            "package.json": "nextjs_package",
            "tsconfig.json": "nextjs_tsconfig",
            "tailwind.config.ts": "nextjs_tailwind",
            "next.config.js": "nextjs_config",
            "app/layout.tsx": "nextjs_layout",
            "app/page.tsx": "nextjs_page",
            "app/globals.css": "nextjs_globals",
            "components/ui/button.tsx": "shadcn_button",
            "components/dashboard/sidebar.tsx": "dashboard_sidebar",
            "lib/utils.ts": "lib_utils",
        },
    },
    "fastapi_service": {
        "id": "fastapi_service",
        "name": "FastAPI Microservice",
        "description": "Async FastAPI service with Pydantic models and SQLAlchemy",
        "framework": "fastapi",
        "features": ["async", "pydantic", "sqlalchemy", "docker"],
        "files": {
            "main.py": "fastapi_main",
            "requirements.txt": "fastapi_requirements",
            "app/__init__.py": "empty_init",
            "app/api/__init__.py": "empty_init",
            "app/api/routes.py": "fastapi_routes",
            "app/models/__init__.py": "empty_init",
            "app/models/schemas.py": "fastapi_schemas",
            "app/core/__init__.py": "empty_init",
            "app/core/config.py": "fastapi_config",
            "Dockerfile": "fastapi_dockerfile",
        },
    },
    "streamlit_dashboard": {
        "id": "streamlit_dashboard",
        "name": "Streamlit Data Dashboard",
        "description": "Interactive data dashboard with Streamlit and Plotly",
        "framework": "streamlit",
        "features": ["plotly", "pandas", "caching"],
        "files": {
            "app.py": "streamlit_app",
            "requirements.txt": "streamlit_requirements",
            "pages/__init__.py": "empty_init",
            "pages/overview.py": "streamlit_overview",
            "pages/analytics.py": "streamlit_analytics",
            "utils/__init__.py": "empty_init",
            "utils/data.py": "streamlit_data_utils",
            ".streamlit/config.toml": "streamlit_config",
        },
    },
    "flask_api": {
        "id": "flask_api",
        "name": "Flask REST API",
        "description": "Flask REST API with SQLAlchemy and Marshmallow",
        "framework": "flask",
        "features": ["sqlalchemy", "marshmallow", "blueprint"],
        "files": {
            "app.py": "flask_app",
            "requirements.txt": "flask_requirements",
            "config.py": "flask_config",
            "models/__init__.py": "empty_init",
            "models/user.py": "flask_user_model",
            "routes/__init__.py": "empty_init",
            "routes/api.py": "flask_api_routes",
            "schemas/__init__.py": "empty_init",
            "schemas/user.py": "flask_user_schema",
        },
    },
}


# =============================================================================
# Template File Contents
# =============================================================================


def _get_template_content(template_key: str, spec: Dict[str, Any]) -> str:
    """Get template file content with spec substitutions."""
    project_name = spec.get("name", "my-project")
    title = spec.get("title", "My Project")
    description = spec.get("description", "A Lancelot-scaffolded project")
    author = spec.get("author", "Lancelot")

    templates = {
        # =================================================================
        # Next.js Templates
        # =================================================================
        "nextjs_package": f'''{{
  "name": "{project_name}",
  "version": "0.1.0",
  "private": true,
  "scripts": {{
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  }},
  "dependencies": {{
    "next": "^14.0.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.0.0",
    "tailwind-merge": "^2.0.0",
    "lucide-react": "^0.292.0"
  }},
  "devDependencies": {{
    "@types/node": "^20.10.0",
    "@types/react": "^18.2.0",
    "typescript": "^5.3.0",
    "tailwindcss": "^3.3.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0"
  }}
}}
''',
        "nextjs_tsconfig": '''{
  "compilerOptions": {
    "target": "es5",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{"name": "next"}],
    "paths": {"@/*": ["./*"]}
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
''',
        "nextjs_tailwind": '''import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
export default config;
''',
        "nextjs_config": '''/** @type {import("next").NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

module.exports = nextConfig;
''',
        "nextjs_layout": f'''import type {{ Metadata }} from "next";
import {{ Inter }} from "next/font/google";
import "./globals.css";

const inter = Inter({{ subsets: ["latin"] }});

export const metadata: Metadata = {{
  title: "{title}",
  description: "{description}",
}};

export default function RootLayout({{
  children,
}}: {{
  children: React.ReactNode;
}}) {{
  return (
    <html lang="en">
      <body className={{inter.className}}>{{children}}</body>
    </html>
  );
}}
''',
        "nextjs_page": f'''import {{ Sidebar }} from "@/components/dashboard/sidebar";

export default function Home() {{
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 p-8">
        <h1 className="text-3xl font-bold mb-4">{title}</h1>
        <p className="text-gray-600">{description}</p>
      </main>
    </div>
  );
}}
''',
        "nextjs_globals": '''@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --foreground-rgb: 0, 0, 0;
  --background-rgb: 255, 255, 255;
}

body {
  color: rgb(var(--foreground-rgb));
  background: rgb(var(--background-rgb));
}
''',
        "shadcn_button": '''import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "border border-input hover:bg-accent hover:text-accent-foreground",
        ghost: "hover:bg-accent hover:text-accent-foreground",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
''',
        "dashboard_sidebar": f'''export function Sidebar() {{
  return (
    <aside className="w-64 bg-gray-100 min-h-screen p-4">
      <h2 className="text-xl font-bold mb-4">{title}</h2>
      <nav>
        <ul className="space-y-2">
          <li><a href="/" className="block p-2 hover:bg-gray-200 rounded">Dashboard</a></li>
          <li><a href="/analytics" className="block p-2 hover:bg-gray-200 rounded">Analytics</a></li>
          <li><a href="/settings" className="block p-2 hover:bg-gray-200 rounded">Settings</a></li>
        </ul>
      </nav>
    </aside>
  );
}}
''',
        "lib_utils": '''import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
''',
        # =================================================================
        # FastAPI Templates
        # =================================================================
        "fastapi_main": f'''"""
{title} - FastAPI Service
{description}
"""
from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings

app = FastAPI(
    title="{title}",
    description="{description}",
    version="0.1.0",
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {{"status": "healthy", "service": "{project_name}"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
''',
        "fastapi_requirements": '''fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
sqlalchemy>=2.0.0
python-dotenv>=1.0.0
''',
        "fastapi_routes": '''from fastapi import APIRouter, HTTPException
from app.models.schemas import Item, ItemCreate

router = APIRouter()

items_db: dict = {}


@router.get("/items")
async def list_items():
    return list(items_db.values())


@router.get("/items/{item_id}")
async def get_item(item_id: str):
    if item_id not in items_db:
        raise HTTPException(status_code=404, detail="Item not found")
    return items_db[item_id]


@router.post("/items")
async def create_item(item: ItemCreate):
    item_id = str(len(items_db) + 1)
    items_db[item_id] = Item(id=item_id, **item.model_dump())
    return items_db[item_id]
''',
        "fastapi_schemas": '''from pydantic import BaseModel
from typing import Optional


class ItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float


class Item(ItemCreate):
    id: str

    class Config:
        from_attributes = True
''',
        "fastapi_config": f'''from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "{title}"
    debug: bool = False
    database_url: str = "sqlite:///./app.db"

    class Config:
        env_file = ".env"


settings = Settings()
''',
        "fastapi_dockerfile": f'''FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
        # =================================================================
        # Streamlit Templates
        # =================================================================
        "streamlit_app": f'''"""
{title} - Streamlit Dashboard
{description}
"""
import streamlit as st

st.set_page_config(
    page_title="{title}",
    page_icon="📊",
    layout="wide",
)

st.title("{title}")
st.markdown("{description}")

# Import pages
from pages import overview, analytics

# Sidebar navigation
page = st.sidebar.selectbox("Navigation", ["Overview", "Analytics"])

if page == "Overview":
    overview.render()
elif page == "Analytics":
    analytics.render()
''',
        "streamlit_requirements": '''streamlit>=1.29.0
plotly>=5.18.0
pandas>=2.1.0
numpy>=1.26.0
''',
        "streamlit_overview": '''import streamlit as st
import pandas as pd


def render():
    st.header("Overview")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total Users", "1,234", "+12%")

    with col2:
        st.metric("Revenue", "$45,678", "+8%")

    with col3:
        st.metric("Active Sessions", "567", "-3%")

    st.subheader("Recent Activity")
    data = pd.DataFrame({
        "Date": pd.date_range(start="2024-01-01", periods=10),
        "Users": [100, 120, 115, 130, 125, 140, 135, 150, 145, 160],
    })
    st.line_chart(data.set_index("Date"))
''',
        "streamlit_analytics": '''import streamlit as st
import plotly.express as px
import pandas as pd


def render():
    st.header("Analytics")

    # Sample data
    df = pd.DataFrame({
        "Category": ["A", "B", "C", "D"],
        "Values": [25, 35, 20, 20],
    })

    fig = px.pie(df, values="Values", names="Category", title="Distribution")
    st.plotly_chart(fig, use_container_width=True)

    # Time series
    dates = pd.date_range(start="2024-01-01", periods=30, freq="D")
    ts_data = pd.DataFrame({
        "Date": dates,
        "Metric A": range(30),
        "Metric B": range(30, 60),
    })

    st.subheader("Time Series")
    st.line_chart(ts_data.set_index("Date"))
''',
        "streamlit_data_utils": '''import pandas as pd
import streamlit as st


@st.cache_data
def load_data(path: str) -> pd.DataFrame:
    """Load and cache data from file."""
    return pd.read_csv(path)


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess dataframe."""
    return df.dropna()
''',
        "streamlit_config": '''[theme]
primaryColor = "#FF6B6B"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F5F5F5"
textColor = "#333333"

[server]
headless = true
enableCORS = false
''',
        # =================================================================
        # Flask Templates
        # =================================================================
        "flask_app": f'''"""
{title} - Flask API
{description}
"""
from flask import Flask
from config import Config
from routes.api import api_bp

app = Flask(__name__)
app.config.from_object(Config)

app.register_blueprint(api_bp, url_prefix="/api/v1")


@app.route("/health")
def health():
    return {{"status": "healthy", "service": "{project_name}"}}


if __name__ == "__main__":
    app.run(debug=True)
''',
        "flask_requirements": '''flask>=3.0.0
flask-sqlalchemy>=3.1.0
flask-marshmallow>=0.15.0
marshmallow-sqlalchemy>=0.29.0
python-dotenv>=1.0.0
''',
        "flask_config": f'''import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or "sqlite:///app.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
''',
        "flask_user_model": '''from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

    def __repr__(self):
        return f"<User {self.username}>"
''',
        "flask_api_routes": '''from flask import Blueprint, jsonify, request

api_bp = Blueprint("api", __name__)


@api_bp.route("/users", methods=["GET"])
def list_users():
    return jsonify({"users": []})


@api_bp.route("/users", methods=["POST"])
def create_user():
    data = request.get_json()
    return jsonify({"user": data}), 201


@api_bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    return jsonify({"user_id": user_id})
''',
        "flask_user_schema": '''from marshmallow import Schema, fields


class UserSchema(Schema):
    id = fields.Int(dump_only=True)
    username = fields.Str(required=True)
    email = fields.Email(required=True)


user_schema = UserSchema()
users_schema = UserSchema(many=True)
''',
        # =================================================================
        # Common Templates
        # =================================================================
        "empty_init": '"""Auto-generated module."""\n',
    }

    return templates.get(template_key, f"# Template not found: {template_key}\n")


# =============================================================================
# TemplateScaffolder Provider
# =============================================================================


class TemplateScaffolder(BaseProvider):
    """
    Template-based UI scaffolding provider.

    Provides deterministic project scaffolding from pre-defined templates.
    This is the required UIBuilder provider - Antigravity is optional.
    """

    def __init__(self, config: Optional[TemplateConfig] = None):
        """
        Initialize the TemplateScaffolder.

        Args:
            config: Optional TemplateConfig (uses defaults if not provided)
        """
        self.config = config or TemplateConfig()
        self._last_health_check: Optional[str] = None

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return "ui_templates"

    @property
    def capabilities(self) -> List[Capability]:
        """List of capabilities this provider implements."""
        return [Capability.UI_BUILDER]

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """
        Check provider health.

        Templates provider is always healthy as it has no external dependencies.
        """
        self._last_health_check = datetime.now(timezone.utc).isoformat()

        return ProviderHealth(
            provider_id=self.provider_id,
            state=ProviderState.HEALTHY,
            version="1.0.0",
            last_check=self._last_health_check,
            capabilities=[c.value for c in self.capabilities],
            metadata={
                "templates_available": len(TEMPLATES),
                "mode": "deterministic",
            },
        )

    # =========================================================================
    # UIBuilder Capability
    # =========================================================================

    def scaffold(
        self,
        template_id: str,
        spec: Dict[str, Any],
        workspace: str,
        mode: UIBuilderMode = UIBuilderMode.DETERMINISTIC,
    ) -> ScaffoldResult:
        """
        Scaffold a UI project from template.

        Args:
            template_id: Template identifier
            spec: Specification with project details
            workspace: Output directory
            mode: Only DETERMINISTIC is supported by this provider

        Returns:
            ScaffoldResult with created files and build status
        """
        if mode != UIBuilderMode.DETERMINISTIC:
            return ScaffoldResult(
                success=False,
                output_path=workspace,
                template_id=template_id,
                error_message="TemplateScaffolder only supports DETERMINISTIC mode",
            )

        if template_id not in TEMPLATES:
            return ScaffoldResult(
                success=False,
                output_path=workspace,
                template_id=template_id,
                error_message=f"Unknown template: {template_id}. Available: {list(TEMPLATES.keys())}",
            )

        template = TEMPLATES[template_id]
        files_created = []

        try:
            # Create workspace directory
            os.makedirs(workspace, exist_ok=True)

            # Create each file from template
            for rel_path, content_key in template["files"].items():
                full_path = os.path.join(workspace, rel_path)

                # Create parent directories
                os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)

                # Get and write content
                content = _get_template_content(content_key, spec)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)

                files_created.append(rel_path)
                logger.debug("Created file: %s", rel_path)

            # Verify build if enabled
            build_verified = False
            if self.config.verify_builds:
                build_verified = self.verify_build(workspace)

            return ScaffoldResult(
                success=True,
                output_path=workspace,
                template_id=template_id,
                files_created=files_created,
                build_verified=build_verified,
            )

        except Exception as e:
            logger.exception("Scaffolding failed")
            return ScaffoldResult(
                success=False,
                output_path=workspace,
                template_id=template_id,
                files_created=files_created,
                error_message=str(e),
            )

    def list_templates(self) -> List[Dict[str, Any]]:
        """List available templates with metadata."""
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "framework": t["framework"],
                "features": t.get("features", []),
            }
            for t in TEMPLATES.values()
        ]

    def verify_build(self, workspace: str) -> bool:
        """
        Verify the scaffolded project builds successfully.

        For Python projects, checks syntax validity.
        For Node projects, verifies package.json is valid.
        """
        # Check for Python files
        py_files = list(Path(workspace).rglob("*.py"))
        for py_file in py_files:
            try:
                with open(py_file, "r") as f:
                    source = f.read()
                compile(source, str(py_file), "exec")
            except SyntaxError as e:
                logger.warning("Python syntax error in %s: %s", py_file, e)
                return False

        # Check for package.json validity
        package_json = os.path.join(workspace, "package.json")
        if os.path.exists(package_json):
            try:
                with open(package_json, "r") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                logger.warning("Invalid package.json: %s", e)
                return False

        return True


# =============================================================================
# Factory Function
# =============================================================================


def create_template_scaffolder(
    verify_builds: bool = True,
    build_timeout_s: int = 300,
) -> TemplateScaffolder:
    """
    Factory function for creating TemplateScaffolder.

    Args:
        verify_builds: Whether to verify builds after scaffolding
        build_timeout_s: Timeout for build verification

    Returns:
        Configured TemplateScaffolder
    """
    config = TemplateConfig(
        verify_builds=verify_builds,
        build_timeout_s=build_timeout_s,
    )
    return TemplateScaffolder(config=config)
