"""Repository-level checks: manifests agree, skills and agents are well formed, Codex agents are in sync."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no front matter"
    block = text.split("---\n", 2)[1]
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in block.splitlines() if ln.strip())}


def test_versions_agree():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = plugin["version"]
    assert market["plugins"][0]["version"] == version
    assert codex["version"] == version
    assert f'version = "{version}"' in pyproject


def test_every_skill_has_matching_name_and_a_description():
    skills = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    assert skills
    for skill in skills:
        fm = _front_matter(skill / "SKILL.md")
        assert fm.get("name") == skill.name
        assert len(fm.get("description", "")) > 40


def test_skill_scripts_referenced_in_skill_md_exist():
    for skill_md in (ROOT / "skills").glob("*/SKILL.md"):
        for rel in re.findall(r"\$SKILL/([\w./-]+)", skill_md.read_text(encoding="utf-8")):
            assert (skill_md.parent / rel).exists(), f"{skill_md.parent.name}: missing {rel}"


def test_agents_have_matching_names():
    for agent in (ROOT / "agents").glob("*.md"):
        fm = _front_matter(agent)
        assert fm.get("name") == agent.stem
        assert fm.get("description")


def test_codex_agents_in_sync():
    sync = _load(ROOT / "scripts/sync_codex_agents.py", "sync_codex_agents")
    assert sync.main(["--check"]) == 0, "run: python scripts/sync_codex_agents.py"
