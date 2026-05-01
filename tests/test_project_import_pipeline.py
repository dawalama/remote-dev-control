import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from remote_dev_ctrl.llm import analyze_existing_project, validate_and_fix_config
from remote_dev_ctrl.scaffold import create_imported_project_rdc_yaml
from remote_dev_ctrl.server.process_discovery import discover_processes, load_adt_config
from remote_dev_ctrl.server.processes import ProcessManager, detect_dev_command


def write_next_project(path: Path):
    (path / "package.json").write_text(json.dumps({
        "name": "website2.0",
        "description": "Website built with Next.js and Builder.io.",
        "scripts": {"dev": "next dev", "build": "next build"},
        "dependencies": {
            "next": "^15.0.0",
            "react": "^19.0.0",
            "@builder.io/react": "^8.0.0",
            "vite": "^6.0.0",
        },
        "devDependencies": {"typescript": "^5.0.0"},
    }))
    (path / "next.config.mjs").write_text("export default {};\n")


def write_vite_project(path: Path):
    (path / "package.json").write_text(json.dumps({
        "name": "vite-app",
        "scripts": {"dev": "vite --host 0.0.0.0"},
        "dependencies": {
            "@vitejs/plugin-react": "^5.0.0",
            "vite": "^6.0.0",
            "react": "^19.0.0",
        },
    }))
    (path / "vite.config.js").write_text("export default {};\n")


def test_next_project_discovery_keeps_npm_script_and_next_port(tmp_path):
    write_next_project(tmp_path)

    processes = discover_processes("website2.0", str(tmp_path), use_llm=False)

    assert len(processes) == 1
    assert processes[0].command == "npm run dev"
    assert processes[0].default_port == 3000


def test_vite_project_discovery_uses_vite_port(tmp_path):
    write_vite_project(tmp_path)

    processes = discover_processes("vite-app", str(tmp_path), use_llm=False)

    assert len(processes) == 1
    assert processes[0].command == "npm run dev"
    assert processes[0].default_port == 5173


def test_generic_npm_dev_port_adjustment_does_not_rewrite_to_vite():
    command = ProcessManager._adjust_command_port(object(), "npm run dev", 4100)

    assert command == "PORT=4100 npm run dev"


def test_direct_dev_command_detection_prefers_next_script_over_vite_dependency(tmp_path):
    write_next_project(tmp_path)

    assert detect_dev_command(str(tmp_path)) == ("npx next dev", 3000)


def test_imported_project_rdc_yaml_includes_next_action(tmp_path):
    write_next_project(tmp_path)

    create_imported_project_rdc_yaml(tmp_path, "website2.0", {
        "description": "Website built with Next.js and Builder.io.",
        "type": "frontend",
        "stack": {"backend": "none", "frontend": "nextjs"},
        "features": ["admin"],
    })

    config = yaml.safe_load((tmp_path / "rdc.yaml").read_text())
    assert config["stack"]["frontend"] == "nextjs"
    assert config["actions"] == [{
        "name": "app",
        "kind": "service",
        "command": "npm run dev",
        "description": "Main application (dev)",
        "port": 3000,
    }]

    loaded = load_adt_config(str(tmp_path))
    assert loaded is not None
    assert loaded[0].command == "npm run dev"
    assert loaded[0].default_port == 3000


def test_existing_project_heuristic_fallback_detects_next(monkeypatch, tmp_path):
    write_next_project(tmp_path)
    monkeypatch.setattr("remote_dev_ctrl.llm.llm_generate", lambda *args, **kwargs: None)

    result = analyze_existing_project(tmp_path)

    assert result["name"] == "website2.0"
    assert result["type"] == "frontend"
    assert result["stack"] == {"backend": "none", "frontend": "nextjs"}
    assert result["features"] == ["admin"]


def test_existing_project_heuristic_detects_next_config_without_package(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "next.config.mjs").write_text("export default {};\n")
    monkeypatch.setattr("remote_dev_ctrl.llm.llm_generate", lambda *args, **kwargs: None)

    result = analyze_existing_project(tmp_path)

    assert result["type"] == "frontend"
    assert result["stack"] == {"backend": "none", "frontend": "nextjs"}


def test_config_validation_normalizes_fullstack_without_backend():
    result = validate_and_fix_config({
        "type": "fullstack",
        "stack": {"backend": "none", "frontend": "nextjs"},
        "database": "none",
        "deployment": "vercel",
        "features": ["admin"],
    }, "Website built with Next.js and Builder.io content management")

    assert result["type"] == "frontend"
    assert result["stack"] == {"backend": "none", "frontend": "nextjs"}
