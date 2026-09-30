import { createSignal, onMount, onCleanup, Show, For, createEffect } from "solid-js";
import {
  clearSession,
  createSubmission,
  fetchSubmission,
  fetchSubmissions,
  fetchToolEvents,
  fetchTools,
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

const actionLabel = {
  pause: "暂停",
  resume: "恢复",
};

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  let m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  if (raw.startsWith("/gate")) return { name: "gate", id: null };
  if (raw.startsWith("/log")) return { name: "log", id: null };
  return { name: "home", id: null };
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [tools, setTools] = createSignal([]);
  const [events, setEvents] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);
  const [busyTool, setBusyTool] = createSignal("");

  const [loginUser, setLoginUser] = createSignal("machinist");
  const [loginPass, setLoginPass] = createSignal("machine123456");

  const [toolCode, setToolCode] = createSignal("");
  const [offsetUm, setOffsetUm] = createSignal("");

  function goHome() {
    location.hash = "#/";
  }

  function goGate() {
    location.hash = "#/gate";
  }

  function goLog() {
    location.hash = "#/log";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  async function loadOverview(silent = false) {
    if (!silent) setLoading(true);
    try {
      const [subs, toolList, log] = await Promise.all([
        fetchSubmissions(),
        fetchTools(),
        fetchToolEvents(),
      ]);
      setRows(subs);
      setTools(toolList);
      setEvents(log);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function loadDetail(id, silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      setDetail(await fetchSubmission(id));
    } catch (e) {
      setError(e.message);
      setDetail(null);
    } finally {
      if (!silent) setLoading(false);
    }
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    if (user()) {
      if (route().name === "detail") loadDetail(route().id);
      else loadOverview();
    }
    // 轮刷：让“甲刀候审时暂停 → 乙刀先被领 → 恢复甲刀 → 甲刀才被领”的顺序实时可见。
    const timer = setInterval(() => {
      const u = getUser();
      if (!u) return;
      const r = readHash();
      if (r.name === "detail") loadDetail(r.id, true);
      else loadOverview(true);
    }, 2000);
    onCleanup(() => {
      window.removeEventListener("hashchange", onHash);
      clearInterval(timer);
    });
  });

  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    else loadOverview();
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
      await loadOverview();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setTools([]);
    setEvents([]);
    setDetail(null);
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await createSubmission(toolCode(), offsetUm());
      setToolCode("");
      setOffsetUm("");
      await loadOverview();
    } catch (err) {
      setError(err.message);
    }
  }

  async function togglePause(code, currentlyPaused) {
    setBusyTool(code);
    setError("");
    try {
      if (currentlyPaused) await resumeTool(code);
      else await pauseTool(code);
      await loadOverview();
      if (route().name === "detail" && detail()?.tool_code === code) {
        await loadDetail(detail().id);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyTool("");
    }
  }

  const navItem = (name, href, label, onClick) => (
    <a
      href={href}
      class={route().name === name ? "active" : ""}
      onClick={(e) => {
        e.preventDefault();
        onClick();
      }}
    >
      {label}
    </a>
  );

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">
            刀补绝对值不超过十二微米判合格，否则超差。后台认领进程用行锁领取待复核；暂停台置为暂停的刀，认领一律跳过，其余刀照常处理，恢复后再领。
          </p>
        </div>
        <Show when={user()}>
          <nav class="topnav">
            {navItem("home", "#/", "复核总览", goHome)}
            {navItem("gate", "#/gate", "暂停台", goGate)}
            {navItem("log", "#/log", "痕迹簿", goLog)}
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
            {user().can_write ? "" : "，只读"}）
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
              <button type="button" class="ghost" onClick={loadOverview} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>刀补 µm</th>
                  <th>闸门</th>
                  <th>状态</th>
                  <th>结论</th>
                  <th>提交时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <For each={rows()}>
                  {(row) => (
                    <tr class={row.is_paused ? "row-paused" : ""}>
                      <td>{row.tool_code}</td>
                      <td>{row.offset_um}</td>
                      <td>
                        <Show
                          when={row.is_paused}
                          fallback={<span class="badge badge-ok">正常·可认领</span>}
                        >
                          <span class="badge badge-paused">已暂停·认领跳过</span>
                        </Show>
                      </td>
                      <td>{statusLabel[row.status] || row.status}</td>
                      <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                        {row.verdict || "—"}
                      </td>
                      <td>{new Date(row.created_at).toLocaleString()}</td>
                      <td>
                        <Show when={user().can_write}>
                          <button
                            type="button"
                            class="ghost"
                            disabled={busyTool() === row.tool_code}
                            onClick={() => togglePause(row.tool_code, row.is_paused)}
                          >
                            {row.is_paused ? "恢复" : "暂停"}
                          </button>
                        </Show>
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

        <Show when={route().name === "gate"}>
          <section class="card">
            <div class="toolbar">
              <h2>暂停台</h2>
              <button type="button" class="ghost" onClick={loadOverview} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <p class="hint">
              暂停态存于库内每刀一行的闸门，认领跳过与本页展示同读此源。暂停的刀候审时一律跳过，其余刀照常认领，恢复后该刀才重新进入认领队列。
            </p>
            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>闸门状态</th>
                  <th>候审单数</th>
                  <th>最后操作人</th>
                  <th>最后操作时间</th>
                  <Show when={user().can_write}><th>操作</th></Show>
                </tr>
              </thead>
              <tbody>
                <For each={tools()}>
                  {(t) => (
                    <tr class={t.is_paused ? "row-paused" : ""}>
                      <td>{t.tool_code}</td>
                      <td>
                        <Show
                          when={t.is_paused}
                          fallback={<span class="badge badge-ok">正常</span>}
                        >
                          <span class="badge badge-paused">已暂停</span>
                        </Show>
                      </td>
                      <td>{t.pending_count}</td>
                      <td>{t.changed_by_name || "—"}</td>
                      <td>{t.changed_at ? new Date(t.changed_at).toLocaleString() : "—"}</td>
                      <Show when={user().can_write}>
                        <td>
                          <button
                            type="button"
                            class="ghost"
                            disabled={busyTool() === t.tool_code}
                            onClick={() => togglePause(t.tool_code, t.is_paused)}
                          >
                            {busyTool() === t.tool_code
                              ? "提交中…"
                              : t.is_paused
                                ? "恢复认领"
                                : "暂停认领"}
                          </button>
                        </td>
                      </Show>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!tools().length && !loading()}>
              <p class="hint">暂无刀具</p>
            </Show>
            <Show when={!user().can_write}>
              <p class="hint">复核员账号只读，不可暂停或恢复。</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "log"}>
          <section class="card">
            <div class="toolbar">
              <h2>痕迹簿</h2>
              <button type="button" class="ghost" onClick={loadOverview} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <p class="hint">每次暂停/恢复均在此留痕，只追加、不可改删。</p>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>刀具</th>
                  <th>动作</th>
                  <th>操作人</th>
                </tr>
              </thead>
              <tbody>
                <For each={events()}>
                  {(ev) => (
                    <tr>
                      <td>{new Date(ev.created_at).toLocaleString()}</td>
                      <td>{ev.tool_code}</td>
                      <td>
                        <span class={ev.action === "pause" ? "badge badge-paused" : "badge badge-ok"}>
                          {actionLabel[ev.action] || ev.action}
                        </span>
                      </td>
                      <td>{ev.actor_name || "—"}</td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!events().length && !loading()}>
              <p class="hint">暂无暂停/恢复动作</p>
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
                  <p>刀具：{d().tool_code}</p>
                  <p>刀补 µm：{d().offset_um}</p>
                  <p>
                    闸门：
                    <Show when={d().is_paused} fallback={<span class="badge badge-ok">正常·可认领</span>}>
                      <span class="badge badge-paused">已暂停·认领跳过</span>
                    </Show>
                  </p>
                  <p>状态：{statusLabel[d().status] || d().status}</p>
                  <p class={d().verdict === "合格" ? "pass" : d().verdict === "超差" ? "fail" : ""}>
                    结论：{d().verdict || "—"}
                  </p>
                  <p>提交时间：{new Date(d().created_at).toLocaleString()}</p>
                  <p>
                    复核时间：
                    {d().reviewed_at ? new Date(d().reviewed_at).toLocaleString() : "—"}
                  </p>
                  <Show when={user().can_write}>
                    <p>
                      <button
                        type="button"
                        class="ghost"
                        disabled={busyTool() === d().tool_code}
                        onClick={() => togglePause(d().tool_code, d().is_paused)}
                      >
                        {d().is_paused ? "恢复认领" : "暂停认领"}
                      </button>
                    </p>
                  </Show>
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
