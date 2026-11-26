from coop.board import _render_readme


def test_readme_links_issues():
    out = _render_readme({"repos": [{"repo": "a/b", "default_budget": 50000}]})
    assert "https://github.com/a/b/issues?q=is%3Aissue+is%3Aopen+label%3Acoop-help" in out and "50k" in out
