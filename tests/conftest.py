import pytest

SAMPLE_README = """# daily-interview-pipeline
설명 줄

## 🖥️ CS (네트워크/OS)

- <details><summary><b>[Q001]</b> TCP와 UDP 차이 <i>(2026-06-17)</i></summary>

  **Q.** TCP와 UDP의 차이는?

  ### 🧑‍💻 나의 답변
  TCP는 연결지향이고 UDP는 비연결입니다.

  ### 🤖 AI 피드백
  좋은 답변입니다.
  </details>

- <details><summary><b>[Q002]</b> OSI 7계층 <i>(2026-06-17)</i></summary>

  **Q.** OSI 7계층을 설명하라.

  ### 🧑‍💻 나의 답변

  ### 🤖 AI 피드백

  </details>

## ☕ Java

## 🌱 Spring Boot

## 🗄️ Database

## 🧩 기타 (Python·FastAPI·Next.js / MSA·CI·CD·대용량·테스트)
"""


@pytest.fixture
def sample_readme():
    return SAMPLE_README
