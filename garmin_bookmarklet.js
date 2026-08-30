// Garmin 一键同步书签脚本（源文件，便于维护）
//
// 用法：
//   1) 浏览器登录 https://connect.garmin.cn/modern/activities （中国区）
//      或 https://connect.garmin.com/modern/activities （国际区）
//   2) 把本项目生成的「压缩单行版」存为浏览器书签（URL 以 javascript: 开头）
//   3) 在活动页点击该书签 → 自动拉近 180 天跑步活动 → 推给本地 Agent → 刷新画像
//
// 原理：脚本在你已登录的 Garmin 会话里运行，用你的 Cookie 调 Garmin 接口，
//       绕过 Cloudflare 风控与 2FA（这是 Agent 无法替你完成的身份验证步骤）。
//       拿到的活动外层 zip 直接 POST 给本地 Agent 的 /api/data/garmin_ingest，
//       由 Agent 端解包并刷新画像。你只需点一下书签。
(function () {
  const AGENT_URL = "http://127.0.0.1:5000/api/data/garmin_ingest";
  const DAYS = 180;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const origin = location.origin;
  const show = (m) => alert(m);

  (async () => {
    try {
      const csrf =
        document.querySelector('meta[name="csrf-token"]')?.content ||
        window.Garmin?.csrfToken ||
        localStorage.getItem("csrfToken") ||
        sessionStorage.getItem("csrfToken") || "";
      const H = { nk: "NT", "x-app-ver": "5.24.1.1" };
      if (csrf) H["connect-csrf-token"] = csrf;

      const listCandidates = [
        "/gc-api/activitylist-service/activities/search/activities",
        "/proxy/activitylist-service/activities/search/activities",
      ];
      let listPath = null;
      for (const c of listCandidates) {
        try {
          const r = await fetch(origin + c + "?start=0&limit=1", { headers: H });
          if (r.ok) { listPath = c; break; }
        } catch (e) {}
      }
      if (!listPath) {
        show("❌ 列表端点探测失败，请确认已登录 Garmin Connect 且在活动页运行");
        return;
      }

      const cutoff = Date.now() - DAYS * 86400000;
      const acts = [];
      for (let start = 0; start < 1e7; start += 20) {
        const r = await fetch(origin + listPath + `?start=${start}&limit=20`, { headers: H });
        const j = await r.json();
        const arr = j?.activityList || (Array.isArray(j) ? j : []);
        if (!arr.length) break;
        for (const a of arr) {
          const t = new Date(a.startTimeLocal || a.startTimeGMT).getTime();
          if (t < cutoff) { start = 1e7; break; }
          const type = a.activityType?.typeKey || a.activityType || "";
          if (type === "running" || (a.activityName || "").includes("跑")) acts.push(a);
        }
        if (arr.length < 20) break;
        await sleep(300);
      }
      if (!acts.length) { show("ℹ️ 近 " + DAYS + " 天没有跑步活动"); return; }

      const dlCandidates = [
        (a) => `/gc-api/download-service/files/activity/${a.activityId}`,
        (a) => `/proxy/download-service/files/activity/${a.activityId}?full=true`,
      ];
      const fd = new FormData();
      let ok = 0, fail = 0;
      for (const a of acts) {
        let buf = null;
        for (const mk of dlCandidates) {
          try {
            const r = await fetch(origin + mk(a), { headers: H });
            if (!r.ok) continue;
            const b = await r.arrayBuffer();
            if (b.byteLength < 50) continue;
            buf = b; break;
          } catch (e) {}
        }
        if (!buf) { fail++; continue; }
        fd.append("files", new Blob([buf], { type: "application/zip" }), `${a.activityId}.zip`);
        ok++; await sleep(500);
      }
      if (!ok) { show("❌ 下载失败 " + fail + " 个，未拿到任何活动"); return; }

      const res = await fetch(AGENT_URL, { method: "POST", body: fd, mode: "cors" });
      const j = await res.json();
      if (j.success) {
        // 同步任务已在 Agent 后台启动，自动打开 Agent 网页展示实时进度
        const agentPage = AGENT_URL.replace("/api/data/garmin_ingest", "/") + "?sync=1";
        try { window.open(agentPage, "_blank"); } catch (e) {}
        show("✅ 已提交同步任务（" + (j.package_count || 0) + " 个活动包）\n" +
             "正在后台解包并刷新画像，请在已打开的 Agent 网页查看实时进度。");
      } else {
        show("❌ Agent 返回错误：" + (j.error || "未知"));
      }
    } catch (e) {
      show(
        "❌ 无法连接本地 Agent (" + AGENT_URL + ")\n请确认：\n" +
        "1) Agent 已启动 (python app.py)\n" +
        "2) 端口为 5000\n" +
        "3) 若浏览器拦截属混合内容限制，可改用 http 访问 Garmin，或见项目文档"
      );
    }
  })();
})();
