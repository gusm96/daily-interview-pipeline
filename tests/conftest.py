import pytest
import storage


@pytest.fixture
def sample_readme():
    q = storage.Question("Q001", "CS", storage.category_for_slug("CS"),
                         "TCP와 UDP 차이", "2026-07-05", "TCP와 UDP의 차이는?")
    return storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(q))
