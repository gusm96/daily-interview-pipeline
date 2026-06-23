import main


def test_get_config_default_absent_returns_5():
    assert main.get_config_default("# readme\n내용") == 5


def test_get_config_default_parses_marker():
    readme = "<!-- config:default=8 -->\n# readme"
    assert main.get_config_default(readme) == 8


def test_set_config_default_inserts_when_absent():
    new, n = main.set_config_default("# readme\n본문\n", 3)
    assert n == 3
    assert "<!-- config:default=3 -->" in new
    assert main.get_config_default(new) == 3


def test_set_config_default_replaces_existing():
    readme = "<!-- config:default=5 -->\n# readme"
    new, n = main.set_config_default(readme, 7)
    assert main.get_config_default(new) == 7
    # 마커가 중복 삽입되지 않음
    assert new.count("config:default") == 1
