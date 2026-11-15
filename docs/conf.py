import os

project = "pXv"
author = "Sebastian Schwindt and contributors"
copyright = "2026, Sebastian Schwindt and contributors"
root_doc = "index"
extensions = ["myst_parser", "sphinxcontrib.mermaid", "sphinx_js"]
exclude_patterns = ["_build", "DEVELOPMENT_PLAN.md"]
nitpicky = True
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]
js_source_path = "../web"
jsdoc_config_path = "jsdoc.json"
html_theme = "sphinx_rtd_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
