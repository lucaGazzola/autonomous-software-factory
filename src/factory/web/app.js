(function () {
  "use strict";

  var STATUS_ORDER = ["OPEN", "BLOCKED", "COMPLETED", "FAILED"];
  var REFRESH_MS = 30000;
  var TIMEOUT_MS = 5000;

  var board = document.getElementById("board");
  var emptyState = document.getElementById("empty-state");
  var fetchTimeEl = document.getElementById("fetch-time");
  var noticeEl = document.getElementById("daemon-notice");
  var nameEl = document.getElementById("factory-name");
  var metaPid = document.getElementById("meta-pid");
  var metaInterval = document.getElementById("meta-interval");
  var metaNext = document.getElementById("meta-next");
  var metaOutcome = document.getElementById("meta-outcome");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function timeEl(label, iso) {
    var span = el("span", null, label + " ");
    var time = el("time", null, formatTime(iso));
    time.dateTime = iso || "";
    span.appendChild(time);
    return span;
  }

  function formatTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatInterval(minutes) {
    if (minutes === null || minutes === undefined) return "—";
    if (minutes === 1) return "1 min";
    return minutes + " mins";
  }

  function buildColumns() {
    STATUS_ORDER.forEach(function (status) {
      var col = document.createElement("section");
      col.className = "status-col";
      col.dataset.status = status;

      var head = el("div", "status-col__head");
      var label = el("div", "status-col__label");
      label.appendChild(el("span", "status-col__dot"));
      label.appendChild(el("span", "status-col__name", status));
      head.appendChild(label);

      var count = el("span", "status-col__count", "0");
      head.appendChild(count);

      var list = el("div", "status-col__list");
      col.appendChild(head);
      col.appendChild(list);
      board.appendChild(col);
    });
  }

  function renderStatus(daemon) {
    var name = daemon.name;
    var pid = daemon.pid;
    var interval = daemon.interval_minutes;
    var nextRun = daemon.next_run_at;
    var lastOutcome = daemon.last_outcome;

    if (name) nameEl.textContent = name;
    metaPid.textContent = pid !== undefined && pid !== null ? String(pid) : "—";
    metaInterval.textContent = formatInterval(interval);
    metaNext.textContent = formatTime(nextRun);
    metaOutcome.textContent = lastOutcome ? String(lastOutcome) : "—";
  }

  function renderTasks(tasks) {
    var total = tasks.length;
    var hasAny = false;

    STATUS_ORDER.forEach(function (status) {
      var col = board.querySelector('.status-col[data-status="' + status + '"]');
      if (!col) return;
      var list = col.querySelector(".status-col__list");
      var count = col.querySelector(".status-col__count");
      var group = tasks.filter(function (t) {
        return (t.status || "OPEN").toUpperCase() === status;
      });
      count.textContent = String(group.length);

      list.textContent = "";
      if (group.length === 0) {
        list.appendChild(el("p", "status-col__empty", "nothing here"));
        return;
      }
      hasAny = true;

      group.forEach(function (task) {
        var card = el("article", "task");

        var top = el("div", "task__top");
        top.appendChild(el("span", "task__id", task.id));
        top.appendChild(el("span", "badge badge--" + status, status));
        card.appendChild(top);

        card.appendChild(el("h3", "task__title", task.title));

        if (task.description) {
          card.appendChild(el("p", "task__desc", task.description));
        }

        var times = el("div", "task__times");
        times.appendChild(timeEl("created", task.created_at));
        times.appendChild(timeEl("updated", task.updated_at));
        card.appendChild(times);

        list.appendChild(card);
      });
    });

    emptyState.hidden = hasAny || total > 0;
  }

  function setDaemonDown(down) {
    noticeEl.hidden = !down;
    fetchTimeEl.parentElement.dataset.stale = down ? "true" : "false";
  }

  function fetchJSON(url) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, TIMEOUT_MS) : null;
    var opts = controller ? { signal: controller.signal } : undefined;
    return fetch(url, opts)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .finally(function () {
        if (timer) clearTimeout(timer);
      });
  }

  function refresh() {
    var now = new Date();
    Promise.all([fetchJSON("api/tasks"), fetchJSON("api/status")])
      .then(function (results) {
        var tasks = results[0] || [];
        var status = results[1] || {};
        renderTasks(tasks);
        renderStatus(status);
        setDaemonDown(false);
        fetchTimeEl.textContent = now.toLocaleTimeString();
      })
      .catch(function () {
        setDaemonDown(true);
      });
  }

  buildColumns();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
