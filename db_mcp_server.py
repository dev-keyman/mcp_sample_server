import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from mcp.server.fastmcp import FastMCP

load_dotenv()

DB_PATH = Path(__file__).parent / "db" / "test.db"

mcp = FastMCP("db-qa")
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)


class GraphState(TypedDict):
    question: str
    schema_context: Optional[str]
    sql: Optional[str]
    rows: Optional[Any]
    answer: Optional[str]
    error: Optional[str]


def _get_pk_info(conn, table: str) -> list[str]:
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return [c[1] for c in cursor.fetchall() if c[5] > 0]


def _get_fk_info(conn, table: str) -> list:
    cursor = conn.execute(f"PRAGMA foreign_key_list({table})")
    return cursor.fetchall()


def _build_schema_context() -> str:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute("SELECT TABLE_NAME, TABLE_COMMENT FROM TABLE_COMMENT")
    tables = cursor.fetchall()

    context = "Database Schema Overview\n"
    for table, comment in tables:
        context += f"\nTable: {table}\nDescription: {comment}\n"
        pk_cols = _get_pk_info(conn, table)
        context += f"Primary Key: {', '.join(pk_cols)}\n"
        cursor.execute(
            "SELECT TABLE_COLUNM_NAME, TABLE_COLUNM_COMMENT FROM TABLE_COLUNM_COMMENT WHERE TABLE_NAME = ?",
            (table,),
        )
        context += "Columns:\n"
        for col, cmt in cursor.fetchall():
            context += f" - {col}: {cmt}\n"

    table_names = [t[0] for t in tables]
    relations = []
    for table in table_names:
        for fk in _get_fk_info(conn, table):
            relations.append(f"{fk[2]} (1) ---- (N) {table} [{fk[4]} -> {fk[3]}]")

    context += "\nRelationships (ERD):\n"
    for r in relations:
        context += f" - {r}\n"

    conn.close()
    return context


@lru_cache(maxsize=1)
def _cached_schema() -> str:
    return _build_schema_context()


# --- LangGraph 노드 ---

def _load_schema(state: GraphState) -> GraphState:
    return {**state, "schema_context": _cached_schema()}


def _generate_sql(state: GraphState) -> GraphState:
    system_prompt = f"""You are a senior SQL engineer.
Generate ONLY a valid SQLite SQL query.

Rules:
- Use only provided schema
- Respect Primary Keys and Foreign Keys
- Use proper JOIN paths based on ERD
- Do NOT explain, output SQL only

Database Schema:
{state["schema_context"]}"""

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["question"]),
    ])
    sql = response.content.replace("```sql", "").replace("```", "").strip()
    return {**state, "sql": sql}


def _validate_sql(state: GraphState) -> GraphState:
    sql = state["sql"].lower()
    if not sql.startswith("select"):
        return {**state, "error": "SELECT 쿼리만 허용됩니다."}
    if any(w in sql for w in ["drop", "delete", "update", "insert"]):
        return {**state, "error": "위험한 쿼리가 감지되었습니다."}
    return state


def _execute_sql(state: GraphState) -> GraphState:
    if state.get("error"):
        return state
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        cursor = conn.execute(state["sql"])
        rows = cursor.fetchall()
        conn.close()
        return {**state, "rows": rows}
    except Exception as e:
        return {**state, "error": str(e)}


def _summarize(state: GraphState) -> GraphState:
    if state.get("error"):
        return {**state, "answer": f"오류: {state['error']}"}

    prompt = f"""테이블 comment 정보를 참고해서 한글로 친절하게 답변해줘.

사용자 질문: {state["question"]}

SQL 실행 결과: {state["rows"]}

결과를 간략하게 설명해줘."""

    response = llm.invoke(prompt)
    return {**state, "answer": response.content}


# --- LangGraph 그래프 빌드 ---

_builder = StateGraph(GraphState)
_builder.add_node("load_schema", _load_schema)
_builder.add_node("generate_sql", _generate_sql)
_builder.add_node("validate_sql", _validate_sql)
_builder.add_node("execute_sql", _execute_sql)
_builder.add_node("summarize", _summarize)

_builder.set_entry_point("load_schema")
_builder.add_edge("load_schema", "generate_sql")
_builder.add_edge("generate_sql", "validate_sql")
_builder.add_edge("validate_sql", "execute_sql")
_builder.add_edge("execute_sql", "summarize")
_builder.add_edge("summarize", END)

_graph = _builder.compile()


# --- MCP Tool ---

@mcp.tool()
def ask_db(question: str) -> str:
    """자연어 질문으로 프로젝트 DB를 조회하고 한글로 답변을 반환한다."""
    result = _graph.invoke({"question": question})
    return result["answer"]


if __name__ == "__main__":
    mcp.run()
