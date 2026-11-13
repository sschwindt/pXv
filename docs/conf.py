import os

project = "pXv"
author = "Sebastian Schwindt and contributors"
root_doc = "index"
extensions = ["myst_parser", "sphinxcontrib.mermaid"]
exclude_patterns = ["_build"]
nitpicky = True
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]
html_theme = "sphinx_rtd_theme"
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
