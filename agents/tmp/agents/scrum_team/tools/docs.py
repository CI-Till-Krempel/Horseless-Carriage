# agents/scrum_team/tools/spec-templates.py
from __future__ import annotations
import json
from typing import Any, Dict
from .base import _configured_repo_root, _project_root

def write_file(path: str, content: str, overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Write content to a repository-relative file path.
    """
    repo_root = _configured_repo_root(tool_context)
    abs_path = (repo_root / path).resolve()
    
    # Safety: ensure the path is within the repo_root
    if not str(abs_path).startswith(str(repo_root.resolve())):
        return {"status": "error", "message": f"Path '{path}' is outside the repository root."}
        
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if abs_path.exists() and not overwrite:
            return {"status": "error", "message": f"File exists: {path}"}
        abs_path.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(abs_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def read_doc(path: str, tool_context=None) -> Dict[str, Any]:
    """
    Read any file within the /spec-templates folder of the repository.
    """
    repo_root = _configured_repo_root(tool_context)
    
    # Enforce spec-templates/ prefix
    clean_path = path.lstrip("/")
    if not clean_path.startswith("spec-templates/"):
        return {"status": "error", "message": f"Access denied. Path '{path}' must be within the 'spec-templates/' directory."}
        
    full_path = (repo_root / clean_path).resolve()
    
    # Safety: ensure the path is within repo_root/spec-templates
    docs_root = (repo_root / "spec-templates").resolve()
    if not str(full_path).startswith(str(docs_root)):
        return {"status": "error", "message": f"Path '{path}' is outside the 'spec-templates/' directory."}
        
    if not full_path.exists():
        return {"status": "error", "message": f"File '{path}' not found."}
        
    try:
        content = full_path.read_text(encoding="utf-8")
        return {"status": "ok", "content": content, "path": str(full_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def upsert_prd(content: str, filename: str, tool_context=None) -> Dict[str, Any]:
    """
    Create or update a Product Requirements Document (PRD) in spec-templates/requirements/.
    - filename: e.g. "PRD-Auth-MVP.md"
    """
    if not filename.endswith(".md"):
        filename += ".md"
    if not filename.startswith("PRD-"):
        filename = "PRD-" + filename
        
    path = f"spec-templates/requirements/{filename}"
    return write_file(path, content, overwrite=True, tool_context=tool_context)

def upsert_srs(content: str, filename: str, tool_context=None) -> Dict[str, Any]:
    """
    Create or update a Software Requirements Specification (SRS) in spec-templates/requirements/.
    - filename: e.g. "SRS-Auth-Backend.md"
    """
    if not filename.endswith(".md"):
        filename += ".md"
    if not filename.startswith("SRS-"):
        filename = "SRS-" + filename
        
    path = f"spec-templates/requirements/{filename}"
    return write_file(path, content, overwrite=True, tool_context=tool_context)

def create_from_template(template_path: str, destination_path: str, substitutions_json: str = "{}", overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a documentation file from a template under spec-templates/.
    - template_path: path relative to repo root (e.g., spec-templates/requirements/TEMPLATE-PRD.md)
    - destination_path: output file path relative to repo root
    - substitutions_json: JSON dict of placeholder -> value. Placeholders formatted as <KEY> in template.
    - overwrite: whether to overwrite existing file
    """
    repo_root = _configured_repo_root(tool_context)
    src = (repo_root / template_path).resolve()
    dst = (repo_root / destination_path).resolve()
    try:
        if not src.exists():
            # Fallback to local project template if not in repo
            src = (_project_root() / template_path).resolve()
            
        if not src.exists():
            return {"status": "error", "message": f"Template not found: {template_path}"}
            
        raw = src.read_text(encoding="utf-8")
        try:
            subs: Dict[str, Any] = json.loads(substitutions_json or "{}")
        except json.JSONDecodeError as e:
            return {"status": "error", "message": f"Invalid JSON: {e}"}
        text = raw
        for k, v in subs.items():
            text = text.replace(f"<{k}>", str(v))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not overwrite:
            return {"status": "error", "message": f"File exists: {destination_path}"}
        dst.write_text(text, encoding="utf-8")
        return {"status": "ok", "path": str(dst)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def seed_repository(overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Copy the spec-templates/ directory from the current project into the configured target repo,
    and create a product-specific README.md.
    Then performs an initial commit and push.
    - overwrite: If True, existing files in the target will be replaced.
    """
    import shutil
    from .github import git_push
    
    proj_root = _project_root()
    repo_root = _configured_repo_root(tool_context)
    if repo_root == proj_root:
        return {"status": "error", "message": "The configured target repository is the same as the project root. Seeding is not allowed here."}

    # Ensure target exists
    repo_root.mkdir(parents=True, exist_ok=True)
    files_seeded = []

    try:
        # Create/Update README.md
        dst_readme = repo_root / "README.md"
        if not dst_readme.exists() or overwrite:
            # Try to build a README from session.state
            vision = tool_context.state.get("product_vision", "").strip()
            goals = tool_context.state.get("product_goals", [])
            
            content = ""
            if vision:
                content = f"# Product Vision\n\n{vision}\n"
            else:
                content = "# Project README\n\nWelcome to your new project repository.\n"
            
            content += "\n<!-- AGENT SAFEGUARD: This README reflects the current product vision and goals. Before proposing changes, check the decision log and existing spec-templates. -->\n"
            
            if goals:
                content += "\n## Product Goals\n"
                for g in goals:
                    content += f"- {g}\n"
            
            content += "\n## Documentation\nSee [spec-templates/README.md](spec-templates/README.md) for details on the repository structure.\n"
            
            dst_readme.write_text(content, encoding="utf-8")
            files_seeded.append("README.md")

        # Copy spec-templates/ directory
        src_docs = proj_root / "spec-templates"
        dst_docs = repo_root / "spec-templates"
        if src_docs.exists():
            if not dst_docs.exists():
                shutil.copytree(src_docs, dst_docs)
                files_seeded.append("spec-templates/")
            elif overwrite:
                # Merge spec-templates/ or clear and copy
                shutil.rmtree(dst_docs)
                shutil.copytree(src_docs, dst_docs)
                files_seeded.append("spec-templates/ (overwritten)")

        # Initial commit and push
        if files_seeded:
            push_res = git_push(
                branch="main", # default to main for seeding
                commit_message="chore: initial seed of README and spec-templates",
                add_all=True,
                tool_context=tool_context
            )
            return {"status": "ok", "seeded": files_seeded, "push": push_res}

        return {"status": "ok", "message": "No new files seeded.", "seeded": []}
    except Exception as e:
        return {"status": "error", "message": f"Seeding failed: {e}"}
