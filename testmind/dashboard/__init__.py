"""TestMind 看板 —— 只读聚合层 + 本机 HTTP 展示。

模块分工：
- `playbook`  知识层：入口分类 / 举一反三 / 硬规则 / 质量维度（纯数据）
- `aggregate` 数据层：扫描 reports / regression / .agent / skills（零依赖、可单测）
- `server`    展示层：stdlib http.server，仅监听 127.0.0.1

设计上**不引入任何第三方依赖**，与 testmind 主包一致（requires-python >=3.10，
dependencies = []）。看板只读，不写任何状态。
"""

__all__ = ["playbook", "aggregate", "server"]
