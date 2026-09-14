# 接口规格 · 秒杀下单服务 (flash-sale)

> 这份文档是**契约**:给"其他 AI 照此实现一个 HTTP 服务(接口逻辑)",同时给 TestMind 照此**自动抽事实、生成并执行闭环测试**。§5 验收矩阵把每条规则一一映射到期望码 + 数据库不变量 + 对应测试 case——实现方据此自检,测试方据此判定。两者同源,不允许各说各话。
> 需求原话(刻意模糊):"支持秒杀下单,要有库存、并发别超卖、能取消和超时、走支付网关。" 本文把它**冻结成精确口径**。

## 0. 实现约束
- 语言/框架不限;必须暴露 HTTP,`POST` 为主,JSON。端口自选(TestMind 用 `prepare_verification {env:{sut:{command, health_check:{url|port}}}}` 拉起)。
- 存储:关系型(SQLite/MySQL 皆可),DDL 见 §2,`CHECK`/`UNIQUE`/`NOT NULL` 必须保留(TestMind 静态预检与 DB 对拍依赖它们)。
- 可控时钟:为可测,`pay/tick/cancel/create` 接受请求头 `X-Test-Now`(unix 秒)注入"当前时间";缺省用真实时钟。
- 外部依赖:支付网关地址可注入;故障测试靠反向代理打它。

## 1. 术语与状态机
`sale_order.status`:**0=PENDING_PAY 1=PAID 2=SHIPPED 3=DONE 4=CANCELLED**
合法迁移(其余动作/状态一律 `409`):
```
PENDING(0)  --pay--> PAID(1)      PENDING(0)  --cancel--> CANCELLED(4)   [回补库存]
PAID(1)     --ship--> SHIPPED(2)  PAID(1)     --cancel--> CANCELLED(4)   [回补库存]
SHIPPED(2)  --complete--> DONE(3)
DONE(3) / CANCELLED(4): 终态,任何动作 409
```

## 2. 数据库 DDL(精确)
```sql
CREATE TABLE IF NOT EXISTS buyer (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  status INTEGER NOT NULL DEFAULT 1,                        -- 1=active 0=frozen
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS product (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK(price_cents BETWEEN 1 AND 1000000),
  stock INTEGER NOT NULL DEFAULT 0 CHECK(stock BETWEEN 0 AND 1000000),   -- 下界0:超发会撞 CHECK
  tenant_id INTEGER NOT NULL REFERENCES buyer(id),
  status INTEGER NOT NULL DEFAULT 1,                        -- 1=on_sale 0=off_shelf
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sale_order (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idem_key TEXT UNIQUE,                                     -- 幂等键
  product_id INTEGER NOT NULL REFERENCES product(id),
  buyer_id INTEGER NOT NULL REFERENCES buyer(id),
  qty INTEGER NOT NULL CHECK(qty BETWEEN 1 AND 100),
  unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents BETWEEN 1 AND 1000000),
  total_cents INTEGER NOT NULL CHECK(total_cents BETWEEN 1 AND 100000000),
  status INTEGER NOT NULL DEFAULT 0,                        -- 见 §1
  expires_at INTEGER,                                       -- PENDING 超时时刻;NULL=不过期
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock_log (                      -- 库存流水:扣减(-)/回补(+) 审计,幂等/超发/守恒对拍靠它
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES product(id),
  order_id INTEGER,
  delta INTEGER NOT NULL,
  at INTEGER NOT NULL);
```

## 3. HTTP 接口
### 3.1 `POST /orders` 下单
请求体(`application/json`,多余字段→`400`):
```json
{ "product_id": 1, "buyer_id": 1, "qty": 10,
  "idem_key": "optional-string", "ttl_seconds": null }
```
响应码:
- `201` 成功 → `{"id": <oid>, "total_cents": <unit*qty>}`;扣减 `product.stock`(原子 `stock=stock-qty WHERE stock>=qty`),写 `sale_order`(status0)+`stock_log`(delta=-qty)。
- `200` 幂等重放:`idem_key` 已存在 → `{"id": <旧oid>, "duplicate": true}`,**不新增行、不重复扣库存**。
- `400` 缺字段/类型错/`qty∉[1,100]`/`ttl_seconds∉[0,86400]`/未知字段/`idem_key` 非串。
- `403` 买家被冻结，或**买家与商品跨租户**（`buyer.tenant_id != product.tenant_id` → `cross_tenant`）。 `404` 买家/商品缺失或已删。 `409` 商品下架，或库存不足(售罄)。
- 库存守卫为 `stock >= qty`（**恰好等于库存必须能成交**，off-by-one 用 `>` 会误拒最后一单）。
- **所有 4xx 禁止任何写库**(下单失败不留痕)。

### 3.2 `POST /orders/{id}/{action}` 状态迁移(action∈ pay|ship|complete|cancel)
- `200` 合法迁移 → `{"status": <new>}`。`pay` 额外:仅未过期且支付网关 `ok` 才通过。
- `cancel`(从 0 或 1)→ 回补库存(`stock=stock+qty`,`stock_log` +qty),幂等:已 CANCELLED 再 cancel→`409`(不得重复回补)。
- `409` 非法迁移 / 已过期(`now>expires_at`)的单被 `pay` / 单不存在(缺失→`404`)。
- `502` `pay` 时支付网关故障(超时/非200/拒连/坏JSON),**且必须零写库**(status 保持、无新流水)。
- 未知路由/非 POST→`405` 带 `Allow`。

### 3.3 `POST /scheduler/tick` 超时自动取消
把 `status=0 且 expires_at<=now 且未删` 的 PENDING 单置 4 并回补库存;**恰好一次**(靠 `status=0` CAS:重复/多实例 tick 不重复回补)。→`200 {"cancelled": n}`。

### 3.4 `GET /openapi.json`
返回本服务的 OpenAPI(含 §3.1 请求体 schema 的 `required/minimum/maximum/pattern`)。TestMind 从它自动抽边界/负例事实。

## 4. 种子数据(造数据;实现方启动时写入,TestMind 关系/并发用例依赖这些确定 id)
```
buyer:   1 alice 有效 | 2 bob 冻结(status0) | 3 carol 他租户有效 | 4 ghost 已删(deleted1)
product: 1 SKU-A price100 stock100000 上架 | 2 SKU-B 下架 | 3 SKU-C 已删
         4 SKU-D price200 stock3  上架(超发靶) | 5 SKU-E price100 stock5 上架(回补/定时靶)
```
每个 id 存在的理由:1 通路径;2/3 触发 409/404;4 触发并发不超卖;5 触发取消/超时回补守恒;冻结/已删买家触发 403/404。业务数据禁止随机。

## 5. 验收矩阵(规则 → 端点 → 期望码 → 数据库不变量 → 对应 case)
| # | 规则(业务事实) | 端点 | 期望 | DB 不变量(必须对拍) | TestMind case id |
|---|---|---|---|---|---|
| R1 | 合法下单成功 | POST /orders | 201 | stock-=qty;sale_order+1;stock_log 有 -qty | P0-HAPPY / P1-BND-qty=1..100 |
| R2 | qty 越界/类型毒化/必填缺失 | POST /orders | 400 | **零写库**(db_no_write) | P1-BND-qty=0/101, P0-REQ-*, P1-TYP-* |
| R3 | 商品下架 409 / 缺失·已删 404 | POST /orders | 409/404 | 零写库 | P0-REL-prod-offshelf / -deleted / -missing |
| R4 | 买家冻结 403 / 缺失·已删 404 | POST /orders | 403/404 | 零写库 | P0-REL-buyer-frozen / -deleted / -missing |
| R5 | 幂等重放只一行 | POST /orders ×2 | 201→200 | `COUNT(idem)=1`,不重复扣库存 | P0-IDEM-replay |
| R6 | 并发不超卖 | POST /orders ×8(stock3) | 恰好 3 个 2xx | `stock=0`,`COUNT(sale_order)=3` | P0-CONC-oversell / REG-001 |
| R7 | 金额口径 `total=unit×qty` | POST /orders qty=7 | 201 | 落库 `total_cents=700,qty=7` | P0-MONEY-total / REG-002 |
| R8 | 取消回补且只一次 | create→cancel→cancel | 409(二次) | `stock` 复原,正流水 `COUNT=1` | P0-STOCK-cancel-restore |
| R9 | 状态机合法链 | pay→ship→complete | 200 | `status=3` | P0-ST-leg-chain |
| R10 | 非法迁移(DONE/PAID/CANCELLED 再 pay) | POST /orders/i/pay | 409 | 无写 | P0-ST-illegal-*, P0-ST-cancel-then-pay |
| R11 | 到期临界 | pay@E-1/E/E+1 | 200/200/409 | after 保持 status0 | P0-TIME-before/at/after |
| R12 | 超时 tick 恰好取消一次 | POST /scheduler/tick ×3 | — | `stock` 只回补一次,正流水 `COUNT=1` | P0-SCH-due/notdue/double-tick, REG-003 |
| R13 | 支付网关故障→502 且零写库 | pay(注入503/超时/拒连/坏JSON) | 502 | `status` 不变,无新流水 | P0-DEP-status503/timeout/refuse/badjson |
| R14 | 网关正常对照(防假绿) | pay(ok) | 200 | 正常 | P0-DEP-ok-control |
| R15 | 跨租户越权拒(买家·商品不同租户) | POST /orders(carol) | 403 | 零写库 | P0-REL-carol |
| R16 | 库存恰好等于数量可成交(守卫 `>=`) | POST /orders qty=stock | 201 | `stock=0` | P0-STOCK-exact |

实现方只要 §5 全绿即视为符合契约;TestMind 用红队(`examples/flash-sale/redteam.py`)对 **11 类** P0 缺陷各注入一个真实坏实现,要求闭环把它从基线 PASS 翻成 FAIL——超发/幂等/金额口径/状态机/到期/依赖故障/超时回补/**跨租户越权**/**幂等窗口竞态**/**库存守卫 off-by-one**/**金额入库错位(响应谎报正确、DB 才说真话,HTTP 层看不见)**,验证这张测试网**确实会红**。

## 6. 用 TestMind 跑这套闭环(三步,§29 门面口径)
1. **喂事实**:`plan_verification {schema_sql:<§2>, openapi:<服务 /openapi.json 或 §3.1 schema>}` 抽边界/负例;再用 `plan_verification {facts:[...]}` 注册 §5 里 DDL/OpenAPI 表达不了的行为规则(`oversell:stock/idempotency:create/money:total/state:transitions/time:expiry/dependency:pay/stock:restore/scheduler:tick/relation:party/relation:tenant`,每条带 `source`)。
2. **接环境**:`prepare_verification {env:{sut:{command:"python sut.py", health_check:{url:"http://127.0.0.1:18201/openapi.json"}}, db:{kind:sqlite,path:...}, tables:[sale_order,stock_log,product,buyer], proxy_upstream:<base>}}`;`pay` 用例把依赖网关指向 FaultProxy。
3. **跑闭环**:边界/负例用 `plan_verification {contract, plan}`(仅从**请求契约**展开)后 `run_verification {}` 执行;R5–R14 用 `plan_verification {cases:[...]}` 提交完整 case(`kind: http|sequence|concurrent|fault` + `setup` fixture + `{{var}}` 模板 + `db_rows/db_count/db_no_write` 断言);`final_gate` 终判;`run_verification {add_regression:{case,reason}}` 把修好的缺陷永久留锚。
参考可跑实现:`examples/flash-sale/sut.py`(契约的"影子实现")、`e2e.py`(闭环)、`redteam.py`(故意的测试)。

## 7. 空白模板(其他 AI 写"新接口文档"时照抄骨架)
```
# 接口规格 · <域名>
0. 需求(模糊原话) + 本文冻结的精确口径
1. 术语与状态机(status 取值 + 合法迁移表)
2. 数据库 DDL(带 CHECK/UNIQUE/NOT NULL/FK —— TestMind 靠它们)
3. HTTP 接口(每端点:方法/路径/请求体/全部响应码+body/副作用/负例是否写库)
4. 种子数据(每行存在的理由;禁随机)
5. 验收矩阵(规则→端点→期望码→DB不变量→case id)★核心:实现方与测试方共用
6. TestMind 接入三步
```
硬约束:每条期望码/DB 不变量必须可追溯到 §2/§3 的某处出处;4xx 默认"禁止写库"除非显式例外;涉及并发/幂等/时间/外部依赖的规则,必须在矩阵里给出对应的"会红"测试,否则视为未覆盖。
