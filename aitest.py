import json
import random
import sqlite3
import streamlit as st
from pathlib import Path

# --- 設定路徑 ---
APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "quiz.db"
QUESTIONS_PATH = APP_DIR / "questions.json"


# -------------------------
# 資料庫功能 (Database)
# -------------------------
def init_db():
    """初始化資料庫"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
              CREATE TABLE IF NOT EXISTS attempts
              (
                  qid
                  TEXT
                  PRIMARY
                  KEY,
                  is_correct
                  INTEGER
                  NOT
                  NULL,
                  last_answer
                  TEXT,
                  correct_answer
                  TEXT
              )
              """)
    conn.commit()
    conn.close()


def load_attempts():
    """讀取所有作答紀錄"""
    init_db()  # 確保表格存在
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT qid, is_correct, last_answer, correct_answer FROM attempts")
    rows = cur.fetchall()
    conn.close()
    # 回傳格式: {qid: {info...}}
    return {r[0]: {"is_correct": r[1], "last_answer": r[2], "correct_answer": r[3]} for r in rows}


def save_attempts_batch(results):
    """
    批次寫入作答紀錄 (優化效能)
    results: list of tuples (qid, is_correct, user_ans, correct_ans)
    """
    conn = sqlite3.connect(DB_PATH)
    # 使用 UPSERT 語法 (SQLite 3.24+)
    conn.executemany("""
                     INSERT INTO attempts(qid, is_correct, last_answer, correct_answer)
                     VALUES (?, ?, ?, ?) ON CONFLICT(qid) DO
                     UPDATE SET
                         is_correct=excluded.is_correct,
                         last_answer=excluded.last_answer,
                         correct_answer=excluded.correct_answer
                     """, [(r["qid"], int(r["is_correct"]), r["user_ans"], r["correct_ans"]) for r in results])
    conn.commit()
    conn.close()


def reset_progress():
    """清空資料庫"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM attempts")
    conn.commit()
    conn.close()


# -------------------------
# 題目載入 (含快取優化)
# -------------------------
@st.cache_data  # <--- 關鍵優化：避免每次重整都讀檔
def load_questions():
    if not QUESTIONS_PATH.exists():
        st.error(f"找不到檔案：{QUESTIONS_PATH}。請確認 questions.json 位於同一目錄。")
        return []

    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            st.error("JSON 格式錯誤，無法解析。")
            return []

    if not isinstance(data, list) or len(data) == 0:
        st.error("JSON 必須是一個非空的列表 (List)。")
        return []

    normalized = []
    seen_ids = set()

    for i, q in enumerate(data):
        # 基本欄位檢查
        required_keys = ["id", "question", "options", "answer"]
        if not all(k in q for k in required_keys):
            st.warning(f"第 {i + 1} 題資料不完整，跳過。")
            continue

        raw_id = int(q["id"])
        if raw_id in seen_ids:
            continue  # 跳過重複 ID
        seen_ids.add(raw_id)

        options = q["options"]
        ans_idx = q["answer"]

        # 確保選項有效性
        if not isinstance(options, list) or len(options) < 2:
            continue
        if not (0 <= ans_idx < len(options)):
            continue

        normalized.append({
            "id": f"Q{raw_id:04d}",  # 格式化 ID 為 Q0001
            "question": q["question"].strip(),
            "choices": [str(x).strip() for x in options],
            "answer": str(options[ans_idx]).strip(),  # 儲存正確答案的文字
            "explanation": q.get("explanation", "").strip()
        })

    return normalized


# -------------------------
# 抽題邏輯
# -------------------------
def pick_questions(all_questions, attempts, n, avoid_seen=True, use_wrong_only=False):
    seen_ids = set(attempts.keys())
    # 錯題定義：在資料庫中且 is_correct 為 0
    wrong_ids = {qid for qid, v in attempts.items() if v["is_correct"] == 0}

    pool = []
    if use_wrong_only:
        # 只從錯題本挑
        pool = [q for q in all_questions if q["id"] in wrong_ids]
        if not pool:
            st.toast("太棒了！錯題本目前是空的 🎉")
    elif avoid_seen:
        # 排除已做過的
        pool = [q for q in all_questions if q["id"] not in seen_ids]
        if not pool:
            st.toast("所有題目都做完囉！可以考慮重置進度。")
    else:
        # 全部混抽
        pool = list(all_questions)

    if not pool:
        return []

    # 取樣數量不超過池子大小
    n = min(int(n), len(pool))
    return random.sample(pool, n)


# -------------------------
# 主程式 (Streamlit UI)
# -------------------------
st.set_page_config(page_title="刷題神器", layout="centered")

# 初始化 Session State
if "picked" not in st.session_state:
    st.session_state["picked"] = []
if "submitted" not in st.session_state:
    st.session_state["submitted"] = False
if "user_answers" not in st.session_state:
    st.session_state["user_answers"] = {}

st.title("🔥 考試刷題神器")
st.caption("隨機抽題 ｜ 錯題本 ｜ 自動記錄進度")

# 1. 載入資料
questions = load_questions()
if not questions:
    st.stop()  # 沒題目就停止

attempts = load_attempts()
total_q = len(questions)
done_q = len(attempts)
correct_q = sum(1 for v in attempts.values() if v["is_correct"] == 1)
accuracy = (correct_q / done_q * 100) if done_q > 0 else 0.0

# Sidebar 設定與統計
with st.sidebar:
    st.header("📊 刷題狀態")
    st.write(f"總題庫：{total_q} 題")
    st.write(f"已完成：{done_q} 題")
    st.write(f"正確率：{accuracy:.1f}%")
    st.progress(min(done_q / total_q, 1.0))

    st.divider()
    st.header("⚙️ 抽題設定")
    n_input = st.number_input("本次題數", 1, 100, 10)
    avoid_seen = st.checkbox("只出「沒做過」的題", value=True)
    wrong_only = st.checkbox("只出「錯題本」的題", value=False)

    if st.button("🚀 開始/重新抽題", use_container_width=True):
        picked = pick_questions(questions, attempts, n_input, avoid_seen, wrong_only)
        st.session_state["picked"] = picked
        st.session_state["submitted"] = False
        st.session_state["user_answers"] = {}  # 重置答案
        st.rerun()

    st.divider()
    if st.button("🗑️ 重置所有進度", type="primary"):
        reset_progress()
        st.cache_data.clear()
        st.session_state.clear()
        st.rerun()

# 2. 顯示題目區域
picked_qs = st.session_state["picked"]

if not picked_qs:
    st.info("👈 請在左側點擊「開始抽題」")
    st.stop()

# 使用 Form 避免每次點選 Radio 就重整頁面
with st.form("quiz_form"):
    st.subheader(f"本次練習：{len(picked_qs)} 題")

    # 顯示每一題
    for i, q in enumerate(picked_qs):
        st.markdown(f"**{i + 1}. {q['question']}**")
        qid = q["id"]

        # 產生選項
        # 注意：key 必須唯一，我們用 qid 綁定
        st.radio(
            "請選擇：",
            q["choices"],
            key=f"ans_{qid}",
            index=None,  # 預設不選
            label_visibility="collapsed"
        )
        st.markdown("---")

    submitted = st.form_submit_button("📝 交卷", use_container_width=True)

# 3. 處理交卷邏輯
if submitted:
    results_to_save = []
    score = 0
    wrong_list = []

    for q in picked_qs:
        qid = q["id"]
        user_ans = st.session_state.get(f"ans_{qid}")
        correct_ans = q["answer"]

        is_correct = (user_ans == correct_ans)
        if is_correct:
            score += 1
        else:
            wrong_list.append({
                "q": q,
                "user_ans": user_ans
            })

        results_to_save.append({
            "qid": qid,
            "is_correct": is_correct,
            "user_ans": user_ans,
            "correct_ans": correct_ans
        })

    # 存入資料庫
    save_attempts_batch(results_to_save)
    st.session_state["submitted"] = True

    # 顯示結果
    final_score = int(score / len(picked_qs) * 100)
    if final_score == 100:
        st.balloons()
        st.success(f"太強了！全對！得分：{final_score}")
    else:
        st.error(f"作答結束！得分：{final_score} (對 {score}/{len(picked_qs)} 題)")

    # 顯示錯題解析
    if wrong_list:
        st.subheader("❌ 錯題檢討")
        for item in wrong_list:
            q = item['q']
            with st.expander(f"題目：{q['question']}", expanded=True):
                st.error(f"你的答案：{item['user_ans']}")
                st.success(f"正確答案：{q['answer']}")
                if q['explanation']:
                    st.info(f"💡 解析：{q['explanation']}")
