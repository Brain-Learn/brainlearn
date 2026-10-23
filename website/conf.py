from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

project = "BrainLearn"
author = "BrainLearn contributors"
copyright = f"{date.today().year}, {author}"
release = os.environ.get("BRAINLEARN_DOCS_VERSION", "development")
version = release
docs_status = os.environ.get("BRAINLEARN_DOCS_STATUS", "Development")
if docs_status not in {"Development", "Pre-release", "Stable", "Archived"}:
    raise ValueError("BRAINLEARN_DOCS_STATUS must be Development, Pre-release, Stable, or Archived")
status_class = docs_status.lower().replace(" ", "-")
version_label = (
    "Development docs" if release == "development" else f"{docs_status} docs · {release}"
)

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_gallery.gen_gallery",
    "sphinx_design",
]

root_doc = "index"
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
myst_enable_extensions = ["colon_fence", "deflist", "fieldlist"]
myst_heading_anchors = 3
exclude_patterns = [
    "_build",
    "node_modules/**",
    "Thumbs.db",
    ".DS_Store",
    "gallery/README.rst",
    "sg_execution_times.rst",
    "auto_examples/sg_execution_times.rst",
]
suppress_warnings = ["autosectionlabel.*"]

autosummary_generate = True
autodoc_typehints = "description"
autoclass_content = "class"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
nitpicky = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "mne": ("https://mne.tools/stable/", None),
}

sphinx_gallery_conf = {
    "examples_dirs": "gallery",
    "gallery_dirs": "auto_examples",
    "filename_pattern": r"plot_.*\.py$",
    "download_all_examples": True,
    "remove_config_comments": True,
    "show_memory": False,
    "abort_on_example_error": True,
    "min_reported_time": 2.0,
    "matplotlib_animations": False,
}

html_theme = "pydata_sphinx_theme"
html_title = "BrainLearn — Documentation"
html_logo = "_static/brainlearn-mark.svg"
html_favicon = "_static/favicon.svg"
html_static_path = ["_static"]
html_css_files = ["css/brainlearn.css"]
html_js_files = ["js/brainlearn.js"]
html_baseurl = os.environ.get(
    "BRAINLEARN_DOCS_BASEURL", "https://brain-learn.github.io/brainlearn/"
)
html_theme_options = {
    "logo": {
        "text": "BrainLearn",
        "image_light": "_static/brainlearn-mark.svg",
        "image_dark": "_static/brainlearn-mark-dark.svg",
    },
    "github_url": "https://github.com/Brain-Learn/brainlearn",
    "use_edit_page_button": True,
    "navbar_align": "content",
    "navbar_center": ["navbar-nav"],
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "navbar_persistent": ["search-button"],
    "header_links_before_dropdown": 5,
    "show_toc_level": 2,
    "navigation_depth": 3,
    "show_nav_level": 1,
    "announcement": (
        f'<span class="bl-status bl-status--{status_class}">{version_label}</span> '
        "BrainLearn is experimental research software. The EEG methods review "
        'is pending; see <a href="status.html">project status</a>.'
    ),
}
html_context = {
    "github_user": "Brain-Learn",
    "github_repo": "brainlearn",
    "github_version": "main" if release == "development" else release,
    "doc_path": "website",
    "docs_version": release,
    "docs_status": docs_status,
}
html_show_sourcelink = True
html_last_updated_fmt = "%Y-%m-%d"
html_copy_source = False

linkcheck_ignore = [
    r"https://doi.org/.*",
    r"https://github.com/Brain-Learn/brainlearn/(blob|tree)/.*",
]
linkcheck_retries = 2
linkcheck_timeout = 20
