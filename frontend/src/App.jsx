import { createSignal, onMount, onCleanup, Show, For, createEffect } from "solid-js";
import {
  clearSession,
  createSubmission,
  fetchPauseLogs,
  fetchSubmission,
  fetchSubmissions,
  fetchToolPauses,
  getUser,
  login,
  pauseTool,
  resumeTool,
  setSession,
} from "./api";

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已完成",
};

const roleLabel = {
  machinist: "操作员",
  auditor: "复核员",
};

const pauseActionLabel = {
  pause: "暂停",
  resume: "恢复",
};

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  let m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  if (raw === "/pause") return { name: "pause", id: null };
  if (raw === "/logs") return { name: "logs", id: null };
  return { name: "home", id: null };
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [pauses, setPauses] = createSignal([]);
  const [logs, setLogs] = createSignal([]);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [busyCode, setBusyCode] = createSignal("");

  const [loginUser, setLoginUser] = createSignal("machinist");
  const [loginPass, setLoginPass] = createSignal("machine123456");

  const [toolCode, setToolCode] = createSignal("");
  const [offsetUm, setOffsetUm] = createSignal("");
  const [pauseCode, setPauseCode] = createSignal("");

  function goHome() {
    location.hash = "#/";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  async function loadRows(silent = false) {
    if (!silent) setLoading(true);
    try {
      setRows(await fetchSubmissions());
      setError("");
    } catch (e) {
      if (!silent) setError(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function loadDetail(id, silent = false) {
    if (!silent) setLoading(true);
    try {
      setDetail(await fetchSubmission(id));
      setError("");
    } catch (e) {
      if (!silent) {
        setError(e.message);
        setDetail(null);
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function loadPauses(silent = false) {
    if (!silent) setLoading(true);
    try {
      setPauses(await fetchToolPauses());
      setError("");
    } catch (e) {
      if (!silent) setError(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function loadLogs(silent = false) {
    if (!silent) setLoading(true);
    try {
      setLogs(await fetchPauseLogs());
      setError("");
    } catch (e) {
      if (!silent) setError(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  function refreshCurrent(silent = true) {
    const r = route();
    if (!user()) return;
    if (r.name === "home") loadRows(silent);
    else if (r.name === "pause") loadPauses(silent);
    else if (r.name === "logs") loadLogs(silent);
    else if (r.name === "detail" && r.id) loadDetail(r.id, silent);
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    if (user()) refreshCurrent(false);
    // 轮询让暂停 / 恢复 / 认领的同库状态变化即时可见。
    const timer = setInterval(() => refreshCurrent(true), 2000);
    onCleanup(() => {
      window.removeEventListener("hashchange", onHash);
      clearInterval(timer);
    });
  });

  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    else if (r.name === "home") loadRows();
    else if (r.name === "pause") loadPauses();
    else if (r.name === "logs") loadLogs();
  });

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await login(loginUser(), loginPass());
      setSession(data.token, {
        username: data.username,
        role: data.role,
        can_write: data.can_write,
      });
      setUser(getUser());
      goHome();
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setDetail(null);
    setPauses([]);
    setLogs([]);
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await createSubmission(toolCode(), offsetUm());
      setToolCode("");
      setOffsetUm("");
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  async function changePause(code, paused) {
    const key = `${paused ? "pause" : "resume"}:${code}`;
    setBusyCode(key);
    setError("");
    try {
      if (paused) await pauseTool(code);
      else await resumeTool(code);
      await Promise.all([loadPauses(), loadLogs(), loadRows(true)]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyCode("");
    }
  }

  async function handlePauseNew(e) {
    e.preventDefault();
    const code = pauseCode().trim();
    if (!code) return;
    setPauseCode("");
    await changePause(code, true);
  }

  const navItems = [
    { name: "home", href: "#/", label: "复核总览" },
    { name: "pause", href: "#/pause", label: "暂停台" },
    { name: "logs", href: "#/logs", label: "痕迹簿" },
  ];

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">
            刀补绝对值不超过十二微米判合格，否则超差。后台认领进程用行锁跳过已占行，并跳过暂停刀领取其它待复核单。
          </p>
        </div>
        <Show when={user()}>
          <nav class="topnav">
            <For each={navItems}>
              {(item) => (
                <a
                  href={item.href}
                  class={route().name === item.name ? "active" : ""}
                  onClick={(e) => {
                    e.preventDefault();
                    location.hash = item.href;
                  }}
                >
                  {item.label}
                </a>
              )}
            </For>
          </nav>
        </Show>
      </header>

      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>

      <Show
        when={user()}
        fallback={
          <section class="card">
            <h2>登录</h2>
            <form onSubmit={handleLogin} class="form">
              <label>
                用户名
                <input
                  value={loginUser()}
                  onInput={(e) => setLoginUser(e.currentTarget.value)}
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginPass()}
                  onInput={(e) => setLoginPass(e.currentTarget.value)}
                />
              </label>
              <button type="submit">进入系统</button>
            </form>
            <p class="hint">操作员 machinist / machine123456；复核员 auditor / audit123456（只读）</p>
          </section>
        }
      >
        <section class="card toolbar">
          <div>
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}
            {user().can_write ? "，可写" : "，只读"}）
          </div>
          <button type="button" class="ghost" onClick={handleLogout}>
            退出
          </button>
        </section>

        <Show when={route().name === "home"}>
          <Show when={user().can_write}>
            <section class="card">
              <h2>提交刀补</h2>
              <form onSubmit={handleSubmit} class="form inline">
                <label>
                  刀具编号
                  <input
                    placeholder="如 T01"
                    value={toolCode()}
                    onInput={(e) => setToolCode(e.currentTarget.value)}
                    required
                  />
                </label>
                <label>
                  刀补（微米）
                  <input
                    type="number"
                    value={offsetUm()}
                    onInput={(e) => setOffsetUm(e.currentTarget.value)}
                    required
                  />
                </label>
                <button type="submit">提交待复核</button>
              </form>
            </section>
          </Show>

          <section class="card">
            <div class="toolbar">
              <h2>复核列表</h2>
              <button type="button" class="ghost" onClick={() => loadRows()} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>刀补 µm</th>
                  <th>状态</th>
                  <th>结论</th>
                  <th>提交时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <For each={rows()}>
                  {(row) => (
                    <tr>
                      <td>
                        {row.tool_code}
                        <Show when={row.tool_paused}>
                          {" "}
                          <span class="badge paused">已暂停认领</span>
                        </Show>
                      </td>
                      <td>{row.offset_um}</td>
                      <td>{statusLabel[row.status] || row.status}</td>
                      <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                        {row.verdict || "—"}
                      </td>
                      <td>{new Date(row.created_at).toLocaleString()}</td>
                      <td>
                        <button type="button" class="ghost" onClick={() => goDetail(row.id)}>
                          详情
                        </button>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!rows().length && !loading()}>
              <p class="hint">暂无记录</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "pause"}>
          <section class="card">
            <div class="toolbar">
              <h2>暂停台</h2>
              <button type="button" class="ghost" onClick={() => loadPauses()} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <p class="hint">
              暂停某把刀后，认领进程会跳过该刀的候审单、同轮照常认领其它刀；恢复后该刀候审单才会被领。
              暂停态、认领跳过、本页展示同源。
            </p>

            <Show when={!user().can_write}>
              <p class="hint">复核员账号只读：仅可查看本页与痕迹簿，不能暂停或恢复。</p>
            </Show>

            <Show when={user().can_write}>
              <form onSubmit={handlePauseNew} class="form inline">
                <label>
                  暂停刀具编号
                  <input
                    placeholder="如 T01"
                    value={pauseCode()}
                    onInput={(e) => setPauseCode(e.currentTarget.value)}
                    required
                  />
                </label>
                <button type="submit">暂停该刀</button>
              </form>
            </Show>

            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>是否暂停</th>
                  <th>候审单数</th>
                  <th>最后变更人</th>
                  <th>最后变更时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <For each={pauses()}>
                  {(p) => (
                    <tr>
                      <td>{p.tool_code}</td>
                      <td>
                        <span class={"badge " + (p.is_paused ? "paused" : "resumed")}>
                          {p.is_paused ? "暂停中" : "正常认领"}
                        </span>
                      </td>
                      <td>{p.pending_count}</td>
                      <td>{p.updated_by_name || "—"}</td>
                      <td>{p.updated_at ? new Date(p.updated_at).toLocaleString() : "—"}</td>
                      <td>
                        <Show when={user().can_write}>
                          <button
                            type="button"
                            class={p.is_paused ? "resume" : "pause"}
                            disabled={busyCode() === `pause:${p.tool_code}` || busyCode() === `resume:${p.tool_code}`}
                            onClick={() => changePause(p.tool_code, !p.is_paused)}
                          >
                            {p.is_paused ? "恢复" : "暂停"}
                          </button>
                        </Show>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!pauses().length && !loading()}>
              <p class="hint">暂无刀具记录</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "logs"}>
          <section class="card">
            <div class="toolbar">
              <h2>痕迹簿</h2>
              <button type="button" class="ghost" onClick={() => loadLogs()} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <p class="hint">记录每一次暂停 / 恢复动作，供复核员只读追溯。</p>
            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>动作</th>
                  <th>操作人</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                <For each={logs()}>
                  {(l) => (
                    <tr>
                      <td>{l.tool_code}</td>
                      <td>
                        <span class={"badge " + (l.action === "pause" ? "paused" : "resumed")}>
                          {pauseActionLabel[l.action] || l.action}
                        </span>
                      </td>
                      <td>{l.actor_name || "—"}</td>
                      <td>{new Date(l.created_at).toLocaleString()}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!logs().length && !loading()}>
              <p class="hint">暂无动作记录</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "detail"}>
          <section class="card">
            <div class="toolbar">
              <h2>刀补详情</h2>
              <button type="button" class="ghost" onClick={goHome}>
                返回总览
              </button>
            </div>
            <Show when={detail()} fallback={<p class="hint">{loading() ? "加载中…" : "未找到记录"}</p>}>
              {(d) => (
                <div class="detail-grid">
                  <p>编号：{d().id}</p>
                  <p>
                    刀具：{d().tool_code}
                    <Show when={d().tool_paused}>
                      {" "}<span class="badge paused">已暂停认领</span>
                    </Show>
                  </p>
                  <p>刀补 µm：{d().offset_um}</p>
                  <p>状态：{statusLabel[d().status] || d().status}</p>
                  <p class={d().verdict === "合格" ? "pass" : d().verdict === "超差" ? "fail" : ""}>
                    结论：{d().verdict || "—"}
                  </p>
                  <p>提交时间：{new Date(d().created_at).toLocaleString()}</p>
                  <p>
                    复核时间：
                    {d().reviewed_at ? new Date(d().reviewed_at).toLocaleString() : "—"}
                  </p>
                </div>
              )}
            </Show>
          </section>
        </Show>
      </Show>
    </div>
  );
}

export default App;
