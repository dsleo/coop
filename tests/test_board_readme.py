from coop.board import board_readme


def test_readme_links_issues():
    out = board_readme("g/coop-board", [{"repo": "a/b", "default_budget": 50000}])
    assert "https://github.com/a/b/issues?q=is%3Aissue+is%3Aopen+label%3Acoop-help" in out and "50k" in out
