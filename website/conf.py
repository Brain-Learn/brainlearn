from __future__ import annotations

import os
import re
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
docs_release_stage = os.environ.get("BRAINLEARN_DOCS_RELEASE_STAGE", docs_status)
if docs_status not in {"Development", "Pre-release", "Stable", "Archived"}:
    raise ValueError("BRAINLEARN_DOCS_STATUS must be Development, Pre-release, Stable, or Archived")
if docs_status == "Archived" and docs_release_stage not in {"Stable", "Pre-release"}:
    raise ValueError("Archived docs must preserve their original Stable or Pre-release stage")
if docs_release_stage not in {"Development", "Pre-release", "Stable"}:
    raise ValueError("BRAINLEARN_DOCS_RELEASE_STAGE must identify the version's original stage")
if release != "development" and not re.fullmatch(r"v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", release):
    raise ValueError(
        "BRAINLEARN_DOCS_VERSION must be 'development' or an immutable vMAJOR.MINOR.PATCH tag"
    )
status_class = docs_status.lower().replace(" ", "-")
if docs_status == "Development":
    version_label = "Development docs · development"
elif docs_status == "Archived":
    version_label = f"Archived docs · {release} (was {docs_release_stage.lower()})"
else:
    version_label = f"{docs_status} docs · {release}"
site_root_url = os.environ.get(
    "BRAINLEARN_DOCS_SITE_ROOT_URL", "https://brain-learn.github.io/brainlearn/"
).rstrip("/")
html_baseurl = os.environ.get("BRAINLEARN_DOCS_BASEURL", f"{site_root_url}/")
status_url = f"{html_baseurl.rstrip('/')}/status.html"
switcher_json_url = os.environ.get(
    "BRAINLEARN_DOCS_SWITCHER_JSON_URL",
    f"{site_root_url}/_static/docs-versions.json",
)
if docs_status == "Archived":
    docs_announcement = (
        f'<span class="bl-status bl-status--{status_class}">{version_label}</span> '
        "Historical documentation for the tagged source revision. Its methods and support "
        "claims describe that release and may differ from current BrainLearn; use the "
        f'<a href="{status_url}">version-specific status</a> and version selector.'
    )
elif docs_status == "Development":
    docs_announcement = (
        f'<span class="bl-status bl-status--{status_class}">{version_label}</span> '
        "This is the current development documentation for experimental research software; "
        f'<a href="{status_url}">project status</a> lists current limits.'
    )
else:
    docs_announcement = (
        f'<span class="bl-status bl-status--{status_class}">{version_label}</span> '
        "Documentation built from this immutable release tag. Its claims apply to this "
        f'version only; see <a href="{status_url}">version-specific status</a>.'
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
    "navbar_end": ["version-switcher", "theme-switcher", "navbar-icon-links"],
    "navbar_persistent": ["search-button"],
    "header_links_before_dropdown": 5,
    "show_toc_level": 2,
    "navigation_depth": 3,
    "show_nav_level": 1,
    "switcher": {"json_url": switcher_json_url, "version_match": release},
    "check_switcher": False,
    "announcement": docs_announcement,
}
html_context = {
    "github_user": "Brain-Learn",
    "github_repo": "brainlearn",
    "github_version": "main" if release == "development" else release,
    "doc_path": "website",
    "docs_version": release,
    "docs_status": docs_status,
    "docs_release_stage": docs_release_stage,
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
