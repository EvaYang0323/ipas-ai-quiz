import json
import random
import sqlite3
from pathlib import Path
import streamlit as st

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "quiz.db"
QUESTIONS_PATH = APP_DIR / "questions.json"


# -------------------------
# DB helpers
# -------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            qid TEXT PRIMARY KEY,
            is_correct INTEGER NOT NULL,
            last_answer TEXT,
            correct_answer TEXT
        )
    """)
    return conn


def load_attempts():
    conn = db()
    cur = conn.execute("SELECT qid, is_correct, last_answer, correct_answer FROM attempts")
    rows = cur.fetchall()
    conn.close()
    return {r[0]: {"is_correct": r[1], "last_answer": r[2], "correct_answer": r[3]} for r in rows}


def upsert_attempt(qid: str, is_correct: bool, last_answer: str, correct_answer: str):
    conn = db()
    conn.execute(
        "INSERT INTO attempts(qid, is_correct, last_answer, correct_answer) VALUES(?,?,?,?) "
        "ON CONFLICT(qid) DO UPDATE SET is_correct=excluded.is_correct, last_answer=excluded.last_answer, correct_answer=excluded.correct_answer",
        (qid, int(is_correct), last_answer, correct_answer),
    )
    conn.commit()
    conn.close()


def reset_progress():
    conn = db()
    conn.execute("DELETE FROM attempts")
    conn.commit()
    conn.close()


# -------------------------
# Question loading (✅ adapted to your JSON schema)
# -------------------------
def load_questions():
    """
    Your questions.json schema:
      - id: int
      - question: str
      - options: list[str]
      - answer: int (0-based index)
      - explain: str (optional)
    We normalize to internal schema used by the app:
      - id: "Q0001"
      - question: str
      - choices: list[str]
      - answer: str (correct choice text)
      - explanation: str
    """
    if not QUESTIONS_PATH.exists():
        st.error("找不到 questions.json。請把題庫檔案放在 app.py 同一層。")
        st.stop()

    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or len(data) == 0:
        st.error("questions.json 必須是一個非空的 list。")
        st.stop()

    normalized = []
    seen_ids = set()

    for i, q in enumerate(data):
        # required keys
        for k in ["id", "question", "options", "answer"]:
            if k not in q:
                st.error(f"第 {i+1} 題缺少欄位：{k}")
                st.stop()

        # validate id
        try:
            raw_id = int(q["id"])
        except Exception:
            st.error(f"第 {i+1} 題 id 需為整數（或可轉整數）。目前：{q['id']}")
            st.stop()

        if raw_id in seen_ids:
            st.error(f"題庫中 id 重複：{raw_id}（請修正，否則不重複抽題會壞掉）")
            st.stop()
        seen_ids.add(raw_id)

        # validate question
        question = q["question"]
        if not isinstance(question, str) or not question.strip():
            st.error(f"題目 {raw_id} 的 question 必須是非空字串。")
            st.stop()

        # validate options
        options = q["options"]
        if not isinstance(options, list) or len(options) < 2 or not all(isinstance(x, str) for x in options):
            st.error(f"題目 {raw_id} 的 options 必須是至少 2 個選項的字串 list。")
            st.stop()

        # validate answer index
        ans_idx = q["answer"]
        if not isinstance(ans_idx, int) or not (0 <= ans_idx < len(options)):
            st.error(f"題目 {raw_id} 的 answer 必須是 0~{len(options)-1} 的整數索引。")
            st.stop()

        normalized.append({
            "id": f"Q{raw_id:04d}",
            "question": question.strip(),
            "choices": [x.strip() for x in options],
            "answer": options[ans_idx].strip(),
            "explanation": (q.get("explain", "") or "").strip(),
        })

    return normalized


# -------------------------
# Quiz logic
# -------------------------
def pick_questions(all_questions, attempts, n, avoid_seen=True, use_wrong_only=False):
    seen_ids = set(attempts.keys())
    wrong_ids = {qid for qid, v in attempts.items() if v["is_correct"] == 0}

    if use_wrong_only:
        pool = [q for q in all_questions if q["id"] in wrong_ids]
    elif avoid_seen:
        pool = [q for q in all_questions if q["id"] not in seen_ids]
    else:
        pool = list(all_questions)

    if len(pool) == 0:
        return []

    n = min(int(n), len(pool))
    return random.sample(pool, n)


# -------------------------
# UI
# -------------------------
st.set_page_config(page_title="iPAS AI 應用規劃師 初級｜複習頁", layout="wide")
st.title("iPAS AI 應用規劃師（初級）複習頁 🧠✨")
st.caption("隨機抽題｜錯題本｜已作答不重複（可重置）｜本機保存 SQLite")

questions = load_questions()
attempts = load_attempts()

with st.sidebar:
    st.header("設定")
    total = len(questions)
    st.write(f"題庫總題數：**{total}**")

    default_n = 50 if total >= 50 else total
    n = st.number_input("本次抽題數", min_value=1, max_value=max(1, total), value=default_n, step=1)

    avoid_seen = st.toggle("已作答題目不再出現", value=True)
    wrong_only = st.toggle("只練錯題本", value=False)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("開始新測驗", use_container_width=True):
            picked = pick_questions(questions, attempts, int(n), avoid_seen=avoid_seen, use_wrong_only=wrong_only)
            st.session_state["picked"] = picked
            st.session_state["answers"] = {}
            st.session_state["submitted"] = False

    with col2:
        if st.button("重置進度（清空已作答）", type="secondary", use_container_width=True):
            reset_progress()
            st.session_state.clear()
            st.success("已清空進度。")

picked = st.session_state.get("picked", [])

if not picked:
    st.info("按左側「開始新測驗」。如果你勾了「不重複」又做完了抽不到題，這代表你已經把題庫榨乾了（可重置）。")
    st.stop()

st.subheader(f"本次題目：{len(picked)} 題")

# Render questions
for idx, q in enumerate(picked, start=1):
    st.markdown(f"### {idx}. {q['question']}")
    qid = q["id"]

    # set default
    st.session_state.setdefault("answers", {})
    st.session_state["answers"].setdefault(qid, q["choices"][0])

    st.session_state["answers"][qid] = st.radio(
        "選擇答案",
        options=q["choices"],
        index=q["choices"].index(st.session_state["answers"][qid]) if st.session_state["answers"][qid] in q["choices"] else 0,
        key=f"radio_{qid}",
        label_visibility="collapsed",
    )

st.divider()

if st.button("交卷並存檔", type="primary", use_container_width=True):
    correct = 0
    wrong_list = []

    for q in picked:
        qid = q["id"]
        user_ans = st.session_state["answers"].get(qid)
        is_correct = (user_ans == q["answer"])
        upsert_attempt(qid, is_correct, user_ans, q["answer"])

        if is_correct:
            correct += 1
        else:
            wrong_list.append((q, user_ans))

    st.session_state["submitted"] = True
    score = round(correct / len(picked) * 100, 1)
    st.success(f"得分：{correct}/{len(picked)}（{score} 分）")

    if wrong_list:
        st.warning(f"錯題：{len(wrong_list)} 題（已自動加入錯題本）")
        with st.expander("查看錯題（含解析，如果題庫有提供 explain）", expanded=False):
            for q, user_ans in wrong_list:
                st.markdown(f"**{q['id']}**：{q['question']}")
                st.write(f"你的答案：❌ {user_ans}")
                st.write(f"正確答案：✅ {q['answer']}")
                if q.get("explanation"):
                    st.write(f"解析：{q['explanation']}")
                st.divider()
    else:
        st.balloons()
        st.info("零錯題。錯題本表示：我今天可以下班了嗎？")
