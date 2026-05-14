# Before/After 비교

Before

- tool : multiply
- description : 두 수를 곱한다.
- 변경점 : 정상 결과를 응답한다.
- result : 1*1 = 1

After

- tool : multiply
- description : 두 수를 곱한다.
- 변경점 : 이상 결과를 응답한다.(무조껀 0 응답)
- result : 1*1 = 1

결론 : LLM에서 최종 답변을 응답전에 MCP의 리턴값을 검증하는 것으로 추정
