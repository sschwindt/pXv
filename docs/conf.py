import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

project = "pXv"
author = "Sebastian Schwindt and contributors"
copyright = "2026, Sebastian Schwindt and contributors"
root_doc = "index"
extensions = ["myst_parser", "sphinxcontrib.mermaid", "sphinx.ext.autodoc", "sphinx_js"]
exclude_patterns = ["_build", "DEVELOPMENT_PLAN.md"]
nitpicky = True
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]
autodoc_mock_imports = ["cv2", "scipy", "matplotlib"]
autodoc_typehints = "none"
js_source_path = "../web"
jsdoc_config_path = "jsdoc.json"
html_theme = "sphinx_rtd_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
