import pytest

SAMPLE_README = """# daily-interview-pipeline
설명 줄

## 🖥️ CS (네트워크/OS)

- **[Q001] Q. TCP와 UDP의 차이는?** _(2026-06-17)_
  <details>
  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>

  ### 🧑‍💻 나의 답변
  TCP는 연결지향이고 UDP는 비연결입니다.

  ### 🤖 AI 피드백
  좋은 답변입니다.
  </details>

- **[Q002] Q. OSI 7계층을 설명하라.** _(2026-06-17)_
  <details>
  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>

  ### 🧑‍💻 나의 답변

  ### 🤖 AI 피드백

  </details>

## ☕ Java

## 🌱 Spring Boot

## 🗄️ Database

## ⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)
"""


@pytest.fixture
def sample_readme():
    return SAMPLE_README
