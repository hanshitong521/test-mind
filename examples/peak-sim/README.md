# Peak 模拟环境

模拟 **Token-Mind TaskBundle v2 + 环境档案 + TestMind 全链**，无需真实业务仓与生产网络。

## 组成

| 路径 | 说明 |
|------|------|
| `task-bundle.json` | `task_id` + `spec.verify.change_manifest` + `environment_ref` |
| `env/local.json` | `env_class=test`、base_url、能力开关、fingerprint |
| `env/secrets/` | 模拟密钥库（仅本机进程内解析） |
| `openapi.json` | 三端点：库存 / 分页 / 幂等 |
| `.testmind/tasks/<task_id>/` | 运行后生成的四件套与 state |

SUT 复用 [`../generic/sut.py`](../generic/sut.py)（同进程 HTTP + SQLite 文件）。

## 运行

```bash
cd test-Mind
python scripts/peak_simulation.py
```

或单测：

```bash
python -m unittest tests.test_peak_simulation -v
```

成功时写入 `examples/peak-sim/.testmind/last-simulation.json`，`final=PASS` 且 `secret_leak=false`。
