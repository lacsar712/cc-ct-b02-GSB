# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

**刀具暂停认领**：操作员可在「暂停台」按刀具编号暂停 / 恢复。暂停某把刀后，认领进程跳过该刀候审单、同轮照常认领其它刀；恢复后该刀候审单才会被领。暂停态在库内 `ToolPause` 表中只有一份，闸页展示、认领跳过、单据标记同源；暂停 / 恢复动作写入「痕迹簿」。复核员对暂停台与痕迹簿只读。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Django 5 + django-ninja（ASGI / uvicorn） |
| 前端 | SolidJS + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |
| 鉴权 | JWT（python-jose），令牌存浏览器 localStorage |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3196 |
| 接口 | http://localhost:8196 |
| PostgreSQL | localhost:54396（库名 `cncoffset`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| machinist | machine123456 | 可提交刀补、可暂停 / 恢复刀具 |
| auditor | audit123456 | 只读列表、暂停台、痕迹簿 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 暂停认领的一致性与并发

- **单一数据源**：是否暂停只存在 `desk_toolpause.is_paused` 一处。认领 SQL 用 `NOT IN (暂停刀具子查询)` 排除暂停刀；暂停台列表与总览单据的「已暂停认领」标记都读这张表，不存在「只改闸页」或「只改跳过」的分叉状态。
- **暂停与认领互斥**：暂停事务先 `SELECT ... FOR UPDATE` 锁住该刀全部候审单，再 upsert `ToolPause`、写痕迹簿，三步同一事务提交。worker 认领用 `SKIP LOCKED`，暂停进行中的刀会被跳过并继续认领其它刀。因此「几乎同时一笔点暂停、一笔认领同刀」只可能有一种结局：要么暂停先成（刀仍候审且保持暂停），要么认领先成（刀进入复核中），绝不会「显示已暂停却仍被领走」。
- **恢复后再领**：恢复提交后该刀回到正常认领顺序（按 `created_at, id` 先到先领）。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tool-pauses` | 暂停台：各刀是否暂停（同源）+ 候审单数 |
| POST | `/api/tool-pauses/pause` | 暂停（仅操作员）；重复暂停返回 409 |
| POST | `/api/tool-pauses/resume` | 恢复（仅操作员）；本就正常返回 409 |
| GET | `/api/tool-pause-logs` | 痕迹簿：暂停 / 恢复动作流水 |

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表、暂停台、痕迹簿，没有提交表单与暂停 / 恢复按钮。
4. 甲刀候审单时点「暂停」；乙刀新交应先被领走；恢复甲刀后甲刀才被领。暂停期间认领进程不空闲等待，同轮照常处理其它刀。
5. 暂停台列出的「是否暂停」与总览单据标记、认领跳过行为一致；暂停 / 恢复均在痕迹簿留痕。

## 测试

```bash
pip install -r backend/requirements.txt pytest pytest-django
# 指向任一可达的 PostgreSQL（测试会自动建 / 清测试库）
POSTGRES_HOST=localhost POSTGRES_USER=app POSTGRES_PASSWORD=app \
  pytest backend
```

测试覆盖：暂停跳过 / 其它刀照领 / 恢复后领、多刀暂停、痕迹簿留痕、闸页与库内同源、复核员只读 403、重复操作 409，以及真实 PostgreSQL 多连接下「暂停与认领同刀并发只许一种结局」与「并发重复暂停只落一行一痕」。

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
  desk/models.py      OffsetSubmission / ToolPause / ToolPauseLog
  desk/services.py    认领跳过 claim_next_pending_submission、暂停 set_tool_pause（原子）
  desk/worker.py      后台认领循环（调用同一服务）
  desk/api.py         暂停台 / 暂停 / 恢复 / 痕迹簿接口
frontend/         SolidJS 单页（复核总览 / 暂停台 / 痕迹簿）
docker-compose.yml
PRD.md
```
