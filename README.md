# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

**暂停认领闸**：任意一把刀可在暂停台暂停。暂停后该刀所有候审单被认领进程跳过，其它刀照常认领；恢复后该刀重新进入认领队列。暂停态在库内每刀一行（`ToolPause.is_paused`），暂停台展示、总览/详情徽标、worker 认领跳过三者同读此源，不存在“页面显示已暂停却仍被领走”。暂停/恢复与认领在同一把刀闸行锁上互斥（`FOR UPDATE OF` 同时锁候审单与刀闸行 + `SKIP LOCKED`），两人几乎同时一笔暂停、一笔认领同刀时只有一种结局。每次暂停/恢复写入只追加的痕迹簿。

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
| machinist | machine123456 | 可提交刀补 |
| auditor | audit123456 | 只读列表 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表、暂停台与痕迹簿，没有提交表单，也没有暂停/恢复按钮（写接口返回 403）。
4. **暂停跳过**：甲刀候审单时点「暂停」，随后乙刀新交会先被领；甲刀恢复后，甲刀才被领。期间总览行、暂停台均显示「已暂停·认领跳过」。
5. **同源一致**：暂停台列出的暂停态、复核列表徽标与 worker 跳过逻辑同读库内 `ToolPause.is_paused`，不会出现闸页与实际认领不一致。
6. **痕迹簿**：每次暂停/恢复都记录刀具、动作、操作人、时间，只追加、不可改删，复核员可读。

## 暂停台接口（均需登录）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/tools` | 登录用户 | 暂停台：各刀 `is_paused`、候审单数、最后操作人/时间 |
| POST | `/api/tools/{tool_code}/pause` | 操作员 | 暂停该刀认领（行锁内翻转并写痕迹，重复调用不翻转） |
| POST | `/api/tools/{tool_code}/resume` | 操作员 | 恢复该刀认领 |
| GET | `/api/tool-events` | 登录用户 | 痕迹簿（暂停/恢复动作，按时间倒序） |

`/api/submissions` 与详情返回中增加 `is_paused` 字段，与暂停台同源。

### 无 Postgres 环境跑测试

```bash
DJANGO_SQLITE=1 python manage.py test desk          # 逻辑/API 测试
# 配置好 POSTGRES_* 后（默认）：并发用例随套件一起运行
python manage.py test desk.tests.test_concurrency
```

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
