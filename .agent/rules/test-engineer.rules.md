# TestMind 测试 Agent 规则（脚手架）

1. 续做读 `.agent/state/project_state.json`；禁默认读 transcripts
2. 硬约束写 `decision_state.json` 或本目录 rules，禁只放向量库
3. 阶段结束更新 `current` / `completed`
4. 未执行不得判 PASS：`final_gate` 无 results 一律 NOT_TESTED
5. 仅在 local/test 做故障注入；不为过测改 expected
