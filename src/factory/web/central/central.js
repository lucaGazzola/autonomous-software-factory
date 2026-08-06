/* Central dashboard script: renders the home instance list and the
   per-instance page (kanban backlog + logs/runs/blocker/config tabs).
   Plain JS, no frameworks, refreshes every 30 seconds. */

(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var TIMEOUT_MS = 5000;
  var STATUS_ORDER = ["OPEN", "BLOCKED", "COMPLETED", "FAILED"];
  var TABS = ["backlog", "create", "logs", "runs", "blocker", "config"];

  var page = document.body.dataset.page || "home";
  var match = page === "instance" ? location.pathname.match(/^\/instances\/([^/]+)\/?/) : null;
  var instanceName = match ? decodeURIComponent(match[1]) : null;
  var API = instanceName ? "/api/instances/" + encodeURIComponent(instanceName) + "/" : null;
  var currentTab = "backlog";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function setText(id, text) {
    var node = document.getElementById(id);
    if (node) node.textContent = text;
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

  function timeEl(label, iso) {
    var span = el("span", null, label + " ");
    var time = el("time", null, formatTime(iso));
    time.dateTime = iso || "";
    span.appendChild(time);
    return span;
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

  function setDown(down) {
    var notice = document.getElementById("daemon-notice");
    if (notice) notice.hidden = !down;
    var ft = document.getElementById("fetch-time");
    if (ft && ft.parentElement) {
      ft.parentElement.dataset.stale = down ? "true" : "false";
    }
  }

  function stampFetchTime() {
    setText("fetch-time", new Date().toLocaleTimeString());
  }

  /* ------------------------------------------------------------------ */
  /* Home page                                                           */
  /* ------------------------------------------------------------------ */

  function renderHome(instances) {
    var list = document.getElementById("instance-list");
    var empty = document.getElementById("empty-state");
    if (!list) return;
    list.textContent = "";
    setText("meta-count", String(instances.length));
    if (instances.length === 0) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    instances.forEach(function (inst) {
      var card = el("a", "instance-card", null);
      card.href = "instances/" + encodeURIComponent(inst.name) + "/";

      var head = el("div", "instance-card__head");
      head.appendChild(el("span", "instance-card__name", inst.name));
      head.appendChild(
        el(
          "span",
          "badge badge--" + (inst.daemon_running ? "COMPLETED" : "FAILED"),
          inst.daemon_running ? "running" : "stopped"
        )
      );
      card.appendChild(head);

      var grid = el("div", "instance-card__grid");

      var info = el("div", "instance-card__info");
      info.appendChild(el("span", "instance-card__label", "repo"));
      info.appendChild(el("span", "instance-card__value", inst.repo || "(unavailable)"));
      grid.appendChild(info);

      info = el("div", "instance-card__info");
      info.appendChild(el("span", "instance-card__label", "last outcome"));
      info.appendChild(el("span", "instance-card__value", inst.last_outcome || "—"));
      grid.appendChild(info);

      info = el("div", "instance-card__info");
      info.appendChild(el("span", "instance-card__label", "next run"));
      info.appendChild(el("span", "instance-card__value", formatTime(inst.next_run_at)));
      grid.appendChild(info);

      var counts = el("div", "instance-card__counts");
      STATUS_ORDER.forEach(function (status) {
        counts.appendChild(
          el(
            "span",
            "count-chip count-chip--" + status,
            status + " " + (inst.backlog_counts[status] || 0)
          )
        );
      });
      grid.appendChild(counts);

      card.appendChild(grid);
      list.appendChild(card);
    });
  }

  function refreshHome() {
    fetchJSON("/api/instances")
      .then(function (data) {
        renderHome(data);
        setDown(false);
        stampFetchTime();
      })
      .catch(function () {
        setDown(true);
      });
  }

  /* ------------------------------------------------------------------ */
  /* Instance page: kanban backlog                                       */
  /* ------------------------------------------------------------------ */

  function buildColumns() {
    var board = document.getElementById("tab-backlog");
    if (!board) return;
    STATUS_ORDER.forEach(function (status) {
      var col = document.createElement("section");
      col.className = "status-col";
      col.dataset.status = status;

      var head = el("div", "status-col__head");
      var label = el("div", "status-col__label");
      label.appendChild(el("span", "status-col__dot"));
      label.appendChild(el("span", "status-col__name", status));
      head.appendChild(label);
      head.appendChild(el("span", "status-col__count", "0"));

      var list = el("div", "status-col__list");
      col.appendChild(head);
      col.appendChild(list);
      board.appendChild(col);
    });
  }

  function renderTasks(tasks) {
    var board = document.getElementById("tab-backlog");
    var empty = document.getElementById("empty-state");
    if (!board) return;
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
        card.setAttribute("tabindex", "0");
        card.setAttribute("role", "button");
        card.addEventListener("click", function () {
          openModal(task, card);
        });
        card.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openModal(task, card);
          }
        });
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

    if (empty) empty.hidden = hasAny || tasks.length > 0;

    syncModal(tasks);
  }

  /* ------------------------------------------------------------------ */
  /* Task detail modal                                                   */
  /* ------------------------------------------------------------------ */

  var modalTaskId = null;
  var modalLastFocus = null;

  function listItems(values) {
    var ul = el("ul", "modal__list");
    if (!values || values.length === 0) {
      ul.appendChild(el("li", "modal__empty", "—"));
    } else {
      values.forEach(function (value) {
        ul.appendChild(el("li", null, value));
      });
    }
    return ul;
  }

  function showModalSection(id, show) {
    var node = document.getElementById(id);
    if (node) node.hidden = !show;
  }

  function renderModal(task) {
    if (!task) return;
    setText("task-modal-id", task.id);
    setText("task-modal-title", task.title || "");
    var badge = document.getElementById("task-modal-status");
    if (badge) {
      var status = (task.status || "OPEN").toUpperCase();
      badge.textContent = status;
      badge.className = "badge badge--" + status;
    }

    setText("task-modal-description", task.description || "");
    var acceptance = document.getElementById("task-modal-acceptance");
    if (acceptance) {
      acceptance.textContent = "";
      acceptance.appendChild(listItems(task.acceptance_criteria));
    }
    var dependencies = document.getElementById("task-modal-dependencies");
    if (dependencies) {
      dependencies.textContent = "";
      dependencies.appendChild(listItems(task.dependencies));
    }
    var files = document.getElementById("task-modal-files");
    if (files) {
      files.textContent = "";
      files.appendChild(listItems(task.files_to_modify));
    }
    var command = document.getElementById("task-modal-command");
    if (command) {
      command.textContent = Array.isArray(task.agent_command)
        ? task.agent_command.join(" ")
        : task.agent_command || "";
    }
    var created = document.getElementById("task-modal-created");
    if (created) created.textContent = formatTime(task.created_at);
    var updated = document.getElementById("task-modal-updated");
    if (updated) updated.textContent = formatTime(task.updated_at);

    showModalSection("task-modal-description-section", Boolean(task.description));
    showModalSection(
      "task-modal-acceptance-section",
      task.acceptance_criteria && task.acceptance_criteria.length > 0
    );
    showModalSection(
      "task-modal-dependencies-section",
      task.dependencies && task.dependencies.length > 0
    );
    showModalSection(
      "task-modal-files-section",
      task.files_to_modify && task.files_to_modify.length > 0
    );
    showModalSection("task-modal-command-section", Boolean(task.agent_command));
  }

  function openModal(task, opener) {
    if (!task) return;
    modalTaskId = task.id;
    modalLastFocus = opener || document.activeElement;
    renderModal(task);
    var modal = document.getElementById("task-modal");
    if (modal) {
      modal.hidden = false;
      var dialog = modal.querySelector(".modal");
      if (dialog) dialog.focus();
    }
  }

  function closeModal() {
    var modal = document.getElementById("task-modal");
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    modalTaskId = null;
    if (modalLastFocus && modalLastFocus.focus) modalLastFocus.focus();
    modalLastFocus = null;
  }

  function syncModal(tasks) {
    if (!modalTaskId) return;
    var found = tasks.filter(function (t) {
      return t.id === modalTaskId;
    });
    if (found.length === 0) {
      closeModal();
    } else {
      renderModal(found[0]);
    }
  }

  function renderStatus(status) {
    setText("factory-name", status.name || instanceName);
    var daemon = Boolean(status.daemon_running);
    var badge = document.getElementById("meta-daemon");
    if (badge) {
      badge.textContent = daemon ? "running" : "stopped";
      badge.className = "daemon-badge daemon-badge--" + (daemon ? "running" : "stopped");
    }
    setText("meta-repo", status.repo || "—");
    setText("meta-interval", formatInterval(status.interval_minutes));
    setText("meta-next", formatTime(status.next_run_at));
    setText("meta-outcome", status.last_outcome || "—");
  }

  /* ------------------------------------------------------------------ */
  /* Instance page: non-backlog tabs                                     */
  /* ------------------------------------------------------------------ */

  function outcomeBadge(outcome) {
    if (outcome === "SUCCESS") return "COMPLETED";
    if (outcome === "BLOCKED") return "BLOCKED";
    if (outcome === "ERROR" || outcome === "DIRTY") return "FAILED";
    return "OPEN";
  }

  function renderRuns(runs) {
    var body = document.getElementById("runs-body");
    if (!body) return;
    body.textContent = "";
    if (!runs.length) {
      body.appendChild(el("p", "status-col__empty", "no runs recorded"));
      return;
    }
    var table = el("table", "run-table");
    var thead = el("thead");
    var headRow = el("tr");
    ["finished", "kind", "task", "outcome", "exit", "commit", "duration"].forEach(function (h) {
      headRow.appendChild(el("th", null, h));
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = el("tbody");
    runs.forEach(function (run) {
      var row = el("tr");
      row.appendChild(el("td", null, formatTime(run.finished_at)));
      row.appendChild(el("td", null, run.kind || "—"));
      row.appendChild(el("td", "mono", run.task_id || "—"));
      row.appendChild(el("td", "badge badge--" + outcomeBadge(run.outcome), run.outcome));
      row.appendChild(
        el("td", "mono", run.agent_exit_code === null || run.agent_exit_code === undefined ? "—" : String(run.agent_exit_code))
      );
      row.appendChild(el("td", "mono", run.commit_sha || "—"));
      row.appendChild(
        el("td", "mono", run.duration_seconds === null || run.duration_seconds === undefined ? "—" : run.duration_seconds + "s")
      );
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    body.appendChild(table);
  }

  function renderTextPanel(id, text, fallback) {
    var node = document.getElementById(id);
    if (node) node.textContent = text || fallback;
  }

  function loadTab(tab) {
    if (!API || tab === "backlog" || tab === "create") return;
    if (tab === "logs") {
      fetchJSON(API + "logs?lines=200")
        .then(function (data) {
          renderTextPanel("logs-body", (data.lines || []).join("\n"), "(empty log)");
          setDown(false);
        })
        .catch(function () {
          setDown(true);
        });
    } else if (tab === "runs") {
      fetchJSON(API + "runs?limit=50")
        .then(function (data) {
          renderRuns(data);
          setDown(false);
        })
        .catch(function () {
          setDown(true);
        });
    } else if (tab === "blocker") {
      fetchJSON(API + "blocker")
        .then(function (data) {
          renderTextPanel("blocker-body", data.content, "(no blocker)");
          setDown(false);
        })
        .catch(function () {
          setDown(true);
        });
    } else if (tab === "config") {
      fetchJSON(API + "config")
        .then(function (data) {
          renderTextPanel("config-body", JSON.stringify(data, null, 2), "(no config)");
          setDown(false);
        })
        .catch(function () {
          setDown(true);
        });
    }
  }

  function activate(tab) {
    currentTab = tab;
    TABS.forEach(function (t) {
      var panel = document.getElementById("tab-" + t);
      if (panel) panel.hidden = t !== tab;
    });
    var buttons = document.querySelectorAll(".tab[data-tab]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle("is-active", buttons[i].dataset.tab === tab);
    }
    if (tab !== "backlog") loadTab(tab);
  }

  function refreshInstance() {
    if (!API) return;
    Promise.all([fetchJSON(API + "tasks"), fetchJSON(API + "status")])
      .then(function (results) {
        renderTasks(results[0] || []);
        renderStatus(results[1] || {});
        setDown(false);
        stampFetchTime();
      })
      .catch(function () {
        setDown(true);
      });
  }

  function wireNewTask() {
    var form = document.getElementById("new-task");
    var error = document.getElementById("new-task-error");
    if (!form || !API) return;

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (error) error.hidden = true;

      var title = document.getElementById("task-title").value.trim();
      if (!title) {
        if (error) {
          error.textContent = "title is required";
          error.hidden = false;
        }
        return;
      }
      var description = document.getElementById("task-description").value.trim();
      var rawCriteria = document.getElementById("task-acceptance").value.trim();
      var acceptance = rawCriteria
        ? rawCriteria.split(",").map(function (s) { return s.trim(); }).filter(Boolean)
        : [];

      fetch(API + "tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, description: description, acceptance_criteria: acceptance }),
      })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.json().then(function (data) {
              throw new Error((data && data.error) || "HTTP " + resp.status);
            });
          }
          return resp.json();
        })
        .then(function () {
          form.reset();
          return fetchJSON(API + "tasks");
        })
        .then(function (tasks) {
          renderTasks(tasks || []);
        })
        .catch(function (err) {
          if (error) {
            error.textContent = err.message || "failed to add task";
            error.hidden = false;
          }
        });
    });
  }

  function wire() {
    if (page === "instance") {
      wireNewTask();
      var buttons = document.querySelectorAll(".tab[data-tab]");
      for (var i = 0; i < buttons.length; i++) {
        buttons[i].addEventListener("click", function () {
          activate(this.dataset.tab);
        });
      }

      var closeBtn = document.getElementById("task-modal-close");
      if (closeBtn) closeBtn.addEventListener("click", closeModal);
      var modal = document.getElementById("task-modal");
      if (modal) {
        modal.addEventListener("click", function (event) {
          if (event.target === modal) closeModal();
        });
      }
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeModal();
      });
    }
  }

  /* ------------------------------------------------------------------ */
  /* Boot                                                                */
  /* ------------------------------------------------------------------ */

  function refresh() {
    if (page === "home") {
      refreshHome();
    } else if (page === "instance") {
      refreshInstance();
      if (currentTab !== "backlog") loadTab(currentTab);
    }
  }

  if (page === "instance") {
    buildColumns();
  }
  wire();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
