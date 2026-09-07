# Context Scanner 规则（Phase 1）

目标：在问用户任何问题之前，先把项目里能查到的事实全部查出来。

## 扫描优先级（Java 系优先）

按顺序扫描，命中即记录：

1. **项目规则与文档**：`AGENTS.md`、`CLAUDE.md`、`README.md`、`CONTRIBUTING.md`、`docs/**`、`adr/**`、`architecture/**`
2. **构建与依赖**：`pom.xml`、`build.gradle(.kts)`、`settings.gradle`、`Makefile`、`Dockerfile`、`docker-compose.yml`
3. **数据库**：`*.sql`、`migration/**`、`schema/**`、`entity/**`、`model/**`、MyBatis `*Mapper.xml`、JPA `@Entity` 类
4. **接口**：`controller/**`、`@RestController` / `@RequestMapping` / `@PathVariable` / `@RequestBody` 签名、`openapi*` / `swagger*`
5. **测试**：`src/test/**`——既有的测试风格、断言、已覆盖的业务规则
6. **Git**（环境允许）：`git log --oneline -50`、`git blame` 关键文件，用于识别历史决策
7. **配置**：`application*.yml` / `application*.properties`——只提取结构性事实（端口、数据源类型、开关），**绝不读取或记录密钥**

非 Java 项目：跳过 2–4 的 Java 特有项，走通用扫描（package.json / pyproject.toml / go.mod / router 目录等），规则不变。

## 输出

### `.requirementmind/facts.json`（机读，canonical）

每条事实：

```json
{
  "id": "FACT-001",
  "category": "database",
  "statement": "red_packet.status 字段已存在，tinyint，含注释",
  "source": { "type": "code", "path": "sql/red_packet.sql", "line": 18 },
  "confidence": 1.0,
  "status": "VERIFIED"
}
```

- `category` 取值：`stack` / `structure` / `database` / `api` / `test` / `business_rule` / `history` / `constraint` / `doc`
- `source.type` 取值：`code` / `sql` / `doc` / `test` / `git` / `config`
- `confidence`：直接读到代码/SQL = 1.0；从文档推断 = 0.7；git 历史推断 ≤ 0.6

### `PROJECT_FACTS.md`（人读）

固定章节：`# 已确认项目事实` → `## 技术栈` / `## 目录结构` / `## 数据模型` / `## 已存在接口` / `## 已存在业务规则` / `## 已存在测试` / `## 历史决策` / `## 已知约束`。每条同样带 `path:line` 引用。

## 硬约束

- 只记录**已验证事实**。没扫到的领域留空，写"未扫描到"，禁止臆造。
- 文档与代码冲突时，以代码为权威（文档可能是过期的）；把矛盾本身记为 CONFLICT 线索，交给 Phase 2。
- 配置文件里的密钥、token、连接串密码一律不读取、不落盘。
- 扫描完成后写 `session.json`，阶段游标置 `SCANNED`。
