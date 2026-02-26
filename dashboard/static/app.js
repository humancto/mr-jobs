/**
 * ORBITAL COMMAND — Mission Control Dashboard
 *
 * Alpine.js application with:
 * - Real-time WebSocket event feed
 * - Profile/preferences inline editing
 * - Scheduler countdown timers
 * - Dark-themed Chart.js visualizations
 * - Full job CRUD with expandable detail rows
 */

function missionControl() {
  return {
    // ----- Core State -----
    jobs: [],
    totalJobs: 0,
    stats: {},
    allStatuses: [],
    allCompanies: [],
    expandedJob: null,
    jobNotes: {},
    discovering: false,
    scoring: false,
    loading: false,
    notification: "",
    notificationType: "success",

    // ----- WebSocket -----
    ws: null,
    wsConnected: false,
    _pingTimer: null,

    // ----- Activity Feed -----
    activityFeed: [],
    maxFeedItems: 50,

    // ----- Scheduler -----
    schedulerData: { running: false, jobs: [], last_results: {} },
    _schedulerTimer: null,
    _countdownTimer: null,

    // ----- Profile Editing -----
    profileDirty: false,
    profileEdit: {
      roles: [],
      locations: [],
      min_score: 65,
      primarySkills: [],
      secondarySkills: [],
      favoriteCompanies: [],
      searchQueries: [],
    },
    newRole: "",
    newLocation: "",
    newPrimarySkill: "",
    newSecondarySkill: "",
    newFavCompany: "",
    newSearchQuery: "",

    // ----- View Routing -----
    currentView: "dashboard", // 'dashboard' | 'profile'

    // ----- Auto-Apply -----
    applyState: {
      running: false,
      job_id: null,
      progress: [],
      total: 0,
    },

    // ----- YOLO Mode -----
    yoloState: {
      running: false,
      phase: null,
      cycle: 0,
      continuous: false,
    },
    yoloLog: [],
    showYoloLog: false,

    // ----- Full Profile (for profile editor page) -----
    fullProfile: {},
    profileSaving: false,
    profileSaveTimer: null,
    lastSaveTime: null,

    // ----- Resume Management -----
    resumes: [],
    resumeUploading: false,
    newResumeName: "",
    resumeDragActive: false,

    // ----- Cover Letter Templates -----
    coverLetterTemplates: [],
    newTemplateName: "",
    newTemplateBody: "",

    // ----- Section Collapse State -----
    profileSections: {
      identity: true,
      workAuth: false,
      resumes: true,
      coverLetters: false,
      jobPrefs: true,
      skills: false,
      targets: false,
      searchConfig: false,
      commonAnswers: false,
      schedule: false,
    },

    // ----- Charts -----
    scoreChart: null,
    timelineChart: null,

    // ----- Filters -----
    filters: {
      status: "",
      company: "",
      min_score: null,
      search: "",
      sort_by: "discovered_at",
      sort_order: "desc",
      limit: 50,
      offset: 0,
    },

    // ===================================================================
    // COMPUTED
    // ===================================================================

    get metricCards() {
      const s = this.stats;
      const total =
        (s.discovered || 0) +
        (s.matched || 0) +
        (s.applied || 0) +
        (s.skipped || 0) +
        (s.failed || 0);
      return [
        { label: "Total Jobs", value: total, color: "#e2e8f0" },
        { label: "Matched", value: s.matched || 0, color: "#10b981" },
        { label: "Applied", value: s.applied || 0, color: "#3b82f6" },
        { label: "Today", value: s.today || 0, color: "#8b5cf6" },
        { label: "Avg Score", value: s.avg_match_score || 0, color: "#f59e0b" },
        { label: "Discovered", value: s.discovered || 0, color: "#22d3ee" },
      ];
    },

    get connectionStatus() {
      return this.wsConnected ? "LIVE" : "OFFLINE";
    },

    // ===================================================================
    // LIFECYCLE
    // ===================================================================

    async init() {
      await Promise.all([
        this.fetchJobs(),
        this.fetchStats(),
        this.fetchCompanies(),
        this.fetchStatuses(),
        this.fetchProfile(),
        this.fetchSchedulerStatus(),
      ]);
      this.connectWebSocket();
      this.$nextTick(() => this.initCharts());

      // Refresh scheduler status every 15s
      this._schedulerTimer = setInterval(
        () => this.fetchSchedulerStatus(),
        15000,
      );
      // Update countdown display every second
      this._countdownTimer = setInterval(() => this.updateCountdowns(), 1000);

      this.addFeedItem("System initialized. All stations nominal.", "#22d3ee");
    },

    // ===================================================================
    // DATA FETCHING
    // ===================================================================

    async fetchJobs() {
      this.loading = true;
      try {
        const params = new URLSearchParams();
        if (this.filters.status) params.set("status", this.filters.status);
        if (this.filters.company) params.set("company", this.filters.company);
        if (this.filters.min_score)
          params.set("min_score", this.filters.min_score);
        if (this.filters.search) params.set("search", this.filters.search);
        params.set("sort_by", this.filters.sort_by);
        params.set("sort_order", this.filters.sort_order);
        params.set("limit", this.filters.limit);
        params.set("offset", this.filters.offset);

        const res = await fetch(`/api/jobs?${params}`);
        const data = await res.json();
        this.jobs = data.jobs;
        this.totalJobs = data.total;

        for (const job of this.jobs) {
          if (!(job.id in this.jobNotes)) {
            this.jobNotes[job.id] = job.notes || "";
          }
        }
      } catch (err) {
        this.notify(`Telemetry fetch failed: ${err.message}`, "error");
      } finally {
        this.loading = false;
      }
    },

    async fetchStats() {
      try {
        const res = await fetch("/api/stats");
        this.stats = await res.json();
      } catch (_) {}
    },

    async fetchCompanies() {
      try {
        const res = await fetch("/api/companies");
        this.allCompanies = await res.json();
      } catch (_) {}
    },

    async fetchStatuses() {
      try {
        const res = await fetch("/api/statuses");
        this.allStatuses = await res.json();
      } catch (_) {}
    },

    async fetchProfile() {
      try {
        const res = await fetch("/api/profile");
        const profile = await res.json();

        this.profileEdit.roles = profile.preferences?.roles || [];
        this.profileEdit.locations =
          profile.preferences?.locations || profile.search?.locations || [];
        this.profileEdit.min_score = profile.preferences?.min_match_score || 65;
        this.profileEdit.primarySkills = profile.skills?.primary || [];
        this.profileEdit.secondarySkills = profile.skills?.secondary || [];
        this.profileEdit.favoriteCompanies = profile.favorite_companies || [];
        this.profileEdit.searchQueries = profile.search?.queries || [];
        this.profileDirty = false;
      } catch (err) {
        console.warn("Profile fetch failed:", err);
      }
    },

    async fetchSchedulerStatus() {
      try {
        const res = await fetch("/api/scheduler/status");
        const data = await res.json();
        // Preserve countdown info
        for (const job of data.jobs || []) {
          job._nextRunMs = job.next_run
            ? new Date(job.next_run).getTime()
            : null;
          job.countdown = this._formatCountdown(job._nextRunMs);
        }
        this.schedulerData = data;
      } catch (_) {}
    },

    // ===================================================================
    // PROFILE EDITING
    // ===================================================================

    addItem(field, inputField) {
      const val = this[inputField]?.trim();
      if (!val) return;
      if (!this.profileEdit[field].includes(val)) {
        this.profileEdit[field].push(val);
        this.profileDirty = true;
      }
      this[inputField] = "";
    },

    removeItem(field, index) {
      this.profileEdit[field].splice(index, 1);
      this.profileDirty = true;
    },

    async saveProfile() {
      try {
        const body = {
          preferences: {
            roles: this.profileEdit.roles,
            locations: this.profileEdit.locations,
            min_match_score: this.profileEdit.min_score,
          },
          skills: {
            primary: this.profileEdit.primarySkills,
            secondary: this.profileEdit.secondarySkills,
          },
          favorite_companies: this.profileEdit.favoriteCompanies,
          search: {
            queries: this.profileEdit.searchQueries,
          },
        };

        await fetch("/api/profile", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        this.profileDirty = false;
        this.notify("Mission parameters updated.", "success");
        this.addFeedItem("Profile configuration saved.", "#10b981");
      } catch (err) {
        this.notify(`Profile save failed: ${err.message}`, "error");
      }
    },

    // ===================================================================
    // ACTIONS
    // ===================================================================

    async discover() {
      this.discovering = true;
      this.notify("Discovery scan initiated...", "info");
      this.addFeedItem(
        "Discovery scan initiated. Searching all sources...",
        "#22d3ee",
      );
      try {
        await fetch("/api/discover", { method: "POST" });
      } catch (err) {
        this.discovering = false;
        this.notify(`Scan launch failed: ${err.message}`, "error");
      }
    },

    async scoreAll() {
      this.scoring = true;
      this.notify("Scoring all unscored targets...", "info");
      this.addFeedItem("AI scoring initiated for unscored jobs.", "#8b5cf6");
      try {
        const res = await fetch("/api/score-all", { method: "POST" });
        const data = await res.json();
        if (data.count === 0) {
          this.scoring = false;
          this.notify("No unscored targets found.", "info");
        }
      } catch (err) {
        this.scoring = false;
        this.notify(`Scoring failed: ${err.message}`, "error");
      }
    },

    async changeStatus(jobId, status) {
      try {
        await fetch(`/api/jobs/${jobId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        const job = this.jobs.find((j) => j.id === jobId);
        if (job) job.status = status;
        this.fetchStats();
      } catch (err) {
        this.notify(`Status update failed: ${err.message}`, "error");
      }
    },

    async saveNotes(jobId) {
      try {
        await fetch(`/api/jobs/${jobId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notes: this.jobNotes[jobId] || "" }),
        });
      } catch (err) {
        this.notify(`Notes save failed: ${err.message}`, "error");
      }
    },

    async rescoreJob(jobId) {
      this.notify("Re-scoring target...", "info");
      this.addFeedItem(`Re-scoring job ${jobId.substring(0, 8)}...`, "#8b5cf6");
      try {
        await fetch(`/api/rescore/${jobId}`, { method: "POST" });
      } catch (err) {
        this.notify(`Rescore failed: ${err.message}`, "error");
      }
    },

    async confirmDeleteJob(jobId) {
      if (!confirm("Permanently remove this target from tracking?")) return;
      try {
        await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
        this.jobs = this.jobs.filter((j) => j.id !== jobId);
        this.totalJobs = Math.max(0, this.totalJobs - 1);
        if (this.expandedJob === jobId) this.expandedJob = null;
        this.fetchStats();
      } catch (err) {
        this.notify(`Delete failed: ${err.message}`, "error");
      }
    },

    async copyCoverLetter(text) {
      try {
        await navigator.clipboard.writeText(text);
        this.notify("Cover letter copied.", "success");
      } catch (_) {
        this.notify("Clipboard copy failed.", "warning");
      }
    },

    // ===================================================================
    // AUTO-APPLY
    // ===================================================================

    async applyToJob(jobId) {
      const job = this.jobs.find((j) => j.id === jobId);
      const label = job ? `${job.title} @ ${job.company}` : jobId;
      const mode = confirm(
        `Apply to: ${label}\n\nClick OK for DRY RUN (fill form, don't submit)\nClick Cancel to abort.`,
      );
      // OK = dry run, we don't do live from single-click for safety
      if (mode === false) return;

      try {
        const res = await fetch(`/api/apply/${jobId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: true }),
        });
        const data = await res.json();
        if (res.ok) {
          this.applyState.running = true;
          this.applyState.job_id = jobId;
          this.notify(`Applying to ${label} (dry run)...`, "success");
          this.addFeedItem(`APPLY started: ${label} [DRY RUN]`, "#10b981");
        } else {
          this.notify(data.detail || "Apply failed", "error");
        }
      } catch (err) {
        this.notify(`Apply error: ${err.message}`, "error");
      }
    },

    async batchApply(dryRun) {
      const mode = dryRun ? "DRY RUN" : "LIVE";
      const msg = dryRun
        ? "Start DRY RUN?\n\nThis will fill forms for all matched jobs but NOT submit them. Browser will open so you can watch."
        : "Start LIVE AUTO-APPLY?\n\nThis will ACTUALLY SUBMIT applications for all matched jobs. Are you sure?";

      if (!confirm(msg)) return;

      // Double-confirm for live mode
      if (
        !dryRun &&
        !confirm(
          "FINAL CONFIRMATION: Real applications will be sent. Continue?",
        )
      )
        return;

      try {
        const res = await fetch("/api/apply-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: dryRun, max_count: 10 }),
        });
        const data = await res.json();

        if (data.status === "no_eligible_jobs") {
          this.notify("No eligible matched jobs to apply to.", "warning");
          return;
        }
        if (data.status === "daily_limit_reached") {
          this.notify(
            `Daily limit reached (${data.today}/${data.max}).`,
            "warning",
          );
          return;
        }
        if (res.ok && data.status === "started") {
          this.applyState.running = true;
          this.applyState.total = data.count;
          this.applyState.progress = [];
          this.notify(
            `Batch ${mode}: ${data.count} jobs, ${data.daily_remaining} remaining today`,
            "success",
          );
          this.addFeedItem(
            `BATCH APPLY started: ${data.count} jobs [${mode}]`,
            "#10b981",
          );
        } else {
          this.notify(data.detail || "Batch apply failed", "error");
        }
      } catch (err) {
        this.notify(`Batch apply error: ${err.message}`, "error");
      }
    },

    async cancelApply() {
      try {
        await fetch("/api/apply/cancel", { method: "POST" });
        this.notify("Cancel requested — finishing current job...", "warning");
        this.addFeedItem("APPLY CANCEL requested", "#f59e0b");
      } catch (err) {
        this.notify(`Cancel error: ${err.message}`, "error");
      }
    },

    // ===================================================================
    // YOLO MODE
    // ===================================================================

    async startYolo() {
      const choice = prompt(
        "YOLO MODE — Fully autonomous pipeline\n\n" +
          "Choose mode:\n" +
          "  1 = Single cycle, DRY RUN (safe — fills forms, doesn't submit)\n" +
          "  2 = Single cycle, LIVE (submits real applications)\n" +
          "  3 = Continuous DRY RUN (loops every 6 hours)\n" +
          "  4 = Continuous LIVE (loops + submits — true YOLO)\n\n" +
          "Enter 1, 2, 3, or 4:",
      );
      if (!choice || !["1", "2", "3", "4"].includes(choice.trim())) return;

      const mode = parseInt(choice.trim());
      const dryRun = mode === 1 || mode === 3;
      const continuous = mode === 3 || mode === 4;

      if (!dryRun) {
        if (
          !confirm(
            "LIVE MODE: Real applications will be submitted.\nAre you absolutely sure?",
          )
        )
          return;
      }

      try {
        const res = await fetch("/api/yolo", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dry_run: dryRun,
            continuous: continuous,
            max_apply: 10,
          }),
        });
        const data = await res.json();
        if (res.ok) {
          this.yoloState.running = true;
          this.yoloState.cycle = 0;
          this.yoloLog = [];
          const label = `${continuous ? "CONTINUOUS" : "SINGLE"} ${dryRun ? "DRY RUN" : "LIVE"}`;
          this.notify(`YOLO activated: ${label}`, "success");
          this.addFeedItem(`YOLO MODE: ${label}`, "#fbbf24");
        } else {
          this.notify(data.detail || "YOLO start failed", "error");
        }
      } catch (err) {
        this.notify(`YOLO error: ${err.message}`, "error");
      }
    },

    async cancelYolo() {
      if (!confirm("Abort YOLO mode? Current action will finish first."))
        return;
      try {
        await fetch("/api/yolo/cancel", { method: "POST" });
        this.notify("YOLO abort requested...", "warning");
        this.addFeedItem("YOLO ABORT requested", "#f43f5e");
      } catch (err) {
        this.notify(`Cancel error: ${err.message}`, "error");
      }
    },

    async fetchYoloLog() {
      try {
        const res = await fetch("/api/yolo/log?limit=500");
        const data = await res.json();
        this.yoloLog = data.entries || [];
      } catch (_) {}
    },

    // ===================================================================
    // ACTIVITY FEED
    // ===================================================================

    addFeedItem(msg, color = "#94a3b8") {
      const now = new Date();
      const time = now.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
      this.activityFeed.unshift({ msg, color, time });
      if (this.activityFeed.length > this.maxFeedItems) {
        this.activityFeed.pop();
      }
    },

    // ===================================================================
    // TABLE HELPERS
    // ===================================================================

    toggleExpand(jobId) {
      this.expandedJob = this.expandedJob === jobId ? null : jobId;
    },

    toggleSort(col) {
      if (this.filters.sort_by === col) {
        this.filters.sort_order =
          this.filters.sort_order === "asc" ? "desc" : "asc";
      } else {
        this.filters.sort_by = col;
        this.filters.sort_order = "desc";
      }
      this.resetOffset();
      this.fetchJobs();
    },

    sortIcon(col) {
      if (this.filters.sort_by !== col) return "";
      return this.filters.sort_order === "asc" ? "\u2191" : "\u2193";
    },

    resetFilters() {
      this.filters = {
        status: "",
        company: "",
        min_score: null,
        search: "",
        sort_by: "discovered_at",
        sort_order: "desc",
        limit: 50,
        offset: 0,
      };
      this.fetchJobs();
    },

    resetOffset() {
      this.filters.offset = 0;
    },

    prevPage() {
      this.filters.offset = Math.max(
        0,
        this.filters.offset - this.filters.limit,
      );
      this.fetchJobs();
    },

    nextPage() {
      this.filters.offset += this.filters.limit;
      this.fetchJobs();
    },

    // ===================================================================
    // STYLING HELPERS
    // ===================================================================

    scoreBadgeClass(score) {
      if (score === null || score === undefined) return "none";
      if (score >= 75) return "high";
      if (score >= 50) return "mid";
      return "low";
    },

    statusSelectClass(status) {
      const map = {
        discovered: "border-[#334155] text-[#94a3b8]",
        matched: "border-emerald-800 text-emerald-400",
        applied: "border-blue-800 text-blue-400",
        interviewing: "border-violet-800 text-violet-400",
        offer: "border-emerald-700 text-emerald-300",
        rejected: "border-rose-800 text-rose-400",
        skipped: "border-amber-800 text-amber-400",
        failed: "border-rose-800 text-rose-400",
        withdrawn: "border-[#334155] text-[#64748b]",
        archived: "border-[#334155] text-[#475569]",
      };
      return map[status] || "";
    },

    // ===================================================================
    // FORMATTING
    // ===================================================================

    formatDate(dt) {
      if (!dt) return "\u2014";
      return new Date(dt).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    },

    formatSalary(min, max) {
      const fmt = (n) =>
        n
          ? "$" +
            Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })
          : null;
      const lo = fmt(min);
      const hi = fmt(max);
      if (lo && hi) return `${lo} \u2013 ${hi}`;
      if (lo) return `from ${lo}`;
      if (hi) return `up to ${hi}`;
      return "\u2014";
    },

    _formatCountdown(targetMs) {
      if (!targetMs) return "--:--";
      const diff = targetMs - Date.now();
      if (diff <= 0) return "NOW";
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
      return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    },

    updateCountdowns() {
      if (!this.schedulerData.jobs) return;
      for (const job of this.schedulerData.jobs) {
        if (job._nextRunMs) {
          job.countdown = this._formatCountdown(job._nextRunMs);
        }
      }
    },

    // ===================================================================
    // NOTIFICATION
    // ===================================================================

    notify(msg, type = "success") {
      this.notification = msg;
      this.notificationType = type;
      if (type === "success" || type === "info") {
        setTimeout(() => {
          if (this.notification === msg) this.notification = "";
        }, 5000);
      }
    },

    // ===================================================================
    // WEBSOCKET
    // ===================================================================

    connectWebSocket() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      this.ws = new WebSocket(`${proto}//${location.host}/ws`);

      this.ws.onopen = () => {
        this.wsConnected = true;
        this.addFeedItem("WebSocket link established.", "#10b981");
      };

      this.ws.onclose = () => {
        this.wsConnected = false;
        this.addFeedItem("WebSocket disconnected. Reconnecting...", "#f43f5e");
        setTimeout(() => this.connectWebSocket(), 3000);
      };

      this.ws.onerror = () => {
        this.wsConnected = false;
      };

      this.ws.onmessage = (e) => {
        try {
          this.handleServerEvent(JSON.parse(e.data));
        } catch (_) {}
      };

      if (this._pingTimer) clearInterval(this._pingTimer);
      this._pingTimer = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send("ping");
        }
      }, 25000);
    },

    handleServerEvent(event) {
      switch (event.type) {
        case "discovery_started":
          this.discovering = true;
          this.addFeedItem("Discovery scan in progress...", "#22d3ee");
          break;

        case "discovery_complete":
          this.discovering = false;
          this.notify(
            `Scan complete: ${event.data.new} new targets acquired.`,
            "success",
          );
          this.addFeedItem(
            `Discovery complete: ${event.data.new} new / ${event.data.total} total`,
            "#10b981",
          );
          this.fetchJobs();
          this.fetchStats();
          this.fetchCompanies();
          this.updateCharts();
          break;

        case "discovery_error":
          this.discovering = false;
          this.notify(`Scan error: ${event.data.error}`, "error");
          this.addFeedItem(`Discovery error: ${event.data.error}`, "#f43f5e");
          break;

        case "score_all_complete":
          this.scoring = false;
          this.notify(
            `Scoring complete: ${event.data.count} targets processed.`,
            "success",
          );
          this.addFeedItem(`Scored ${event.data.count} jobs.`, "#10b981");
          this.fetchJobs();
          this.fetchStats();
          this.updateCharts();
          break;

        case "score_all_error":
          this.scoring = false;
          this.notify(`Scoring error: ${event.data.error}`, "error");
          this.addFeedItem(`Scoring error: ${event.data.error}`, "#f43f5e");
          break;

        case "rescore_complete":
          this.notify(`Target rescored: ${event.data.score}`, "success");
          this.addFeedItem(
            `Job rescored: score=${event.data.score}`,
            "#8b5cf6",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "rescore_error":
          this.notify(`Rescore failed: ${event.data.error}`, "error");
          break;

        case "email_check_complete":
          this.addFeedItem(
            `Email check: ${event.data.count} updates found.`,
            "#f59e0b",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "mcp_ingest_complete":
          this.addFeedItem(
            `MCP ingest: ${event.data.ingested || 0} jobs added.`,
            "#22d3ee",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "job_discovered":
          this.addFeedItem(
            `+ ${event.data.title} @ ${event.data.company}`,
            "#22d3ee",
          );
          this.fetchStats();
          break;

        case "job_matched":
          this.addFeedItem(
            `Scored: ${event.data.id?.substring(0, 8)} = ${event.data.score}`,
            "#8b5cf6",
          );
          break;

        case "job_applied":
          this.addFeedItem(
            `Applied: ${event.data.id?.substring(0, 8)} ${event.data.success ? "OK" : "FAIL"}`,
            event.data.success ? "#10b981" : "#f43f5e",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "job_status_changed":
          this.fetchJobs();
          this.fetchStats();
          break;

        // ----- Auto-Apply Events -----
        case "apply_started":
          this.applyState.running = true;
          this.applyState.job_id = event.data.job_id;
          this.addFeedItem(
            `APPLY: ${event.data.title} @ ${event.data.company} [${event.data.dry_run ? "DRY RUN" : "LIVE"}]`,
            "#10b981",
          );
          break;

        case "apply_complete":
          this.applyState.running = false;
          this.applyState.job_id = null;
          this.notify(
            `${event.data.dry_run ? "Dry run" : "Application"} ${event.data.success ? "complete" : "failed"}: ${event.data.title} @ ${event.data.company}`,
            event.data.success ? "success" : "error",
          );
          this.addFeedItem(
            `APPLY ${event.data.success ? "OK" : "FAIL"}: ${event.data.title} @ ${event.data.company}`,
            event.data.success ? "#10b981" : "#f43f5e",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "apply_error":
          this.applyState.running = false;
          this.applyState.job_id = null;
          this.notify(`Apply error: ${event.data.error}`, "error");
          this.addFeedItem(`APPLY ERROR: ${event.data.error}`, "#f43f5e");
          break;

        case "apply_batch_started":
          this.applyState.running = true;
          this.applyState.total = event.data.count;
          this.applyState.progress = [];
          this.addFeedItem(
            `BATCH APPLY started: ${event.data.count} jobs [${event.data.dry_run ? "DRY RUN" : "LIVE"}]`,
            "#10b981",
          );
          break;

        case "apply_progress":
          this.applyState.job_id = event.data.job_id;
          this.addFeedItem(
            `[${event.data.current}/${event.data.total}] ${event.data.title} @ ${event.data.company}`,
            "#22d3ee",
          );
          break;

        case "apply_waiting":
          this.addFeedItem(
            `Rate limit: waiting ${event.data.seconds}s before next...`,
            "#f59e0b",
          );
          break;

        case "apply_batch_complete":
          this.applyState.running = false;
          this.applyState.job_id = null;
          this.applyState.progress = event.data.results || [];
          this.notify(
            `Batch complete: ${event.data.applied}/${event.data.total} ${event.data.dry_run ? "(dry run)" : "submitted"}`,
            "success",
          );
          this.addFeedItem(
            `BATCH COMPLETE: ${event.data.applied}/${event.data.total} jobs`,
            "#10b981",
          );
          this.fetchJobs();
          this.fetchStats();
          this.updateCharts();
          break;

        case "apply_batch_cancelled":
          this.applyState.running = false;
          this.applyState.job_id = null;
          this.notify(
            `Batch cancelled after ${event.data.applied} applications.`,
            "warning",
          );
          this.addFeedItem(
            `BATCH CANCELLED at job ${event.data.cancelled_at}`,
            "#f59e0b",
          );
          this.fetchJobs();
          this.fetchStats();
          break;

        case "apply_batch_error":
          this.applyState.running = false;
          this.applyState.job_id = null;
          this.notify(`Batch error: ${event.data.error}`, "error");
          this.addFeedItem(`BATCH ERROR: ${event.data.error}`, "#f43f5e");
          break;

        // ----- YOLO Events -----
        case "yolo_cycle_start":
          this.yoloState.running = true;
          this.yoloState.cycle = event.data.cycle;
          this.addFeedItem(
            `YOLO CYCLE ${event.data.cycle} [${event.data.dry_run ? "DRY" : "LIVE"}]`,
            "#fbbf24",
          );
          break;

        case "yolo_phase":
          this.yoloState.phase = event.data.phase;
          {
            const colors = {
              discover: "#22d3ee",
              score: "#8b5cf6",
              apply: "#10b981",
            };
            this.addFeedItem(
              `YOLO → ${event.data.phase.toUpperCase()}`,
              colors[event.data.phase] || "#fbbf24",
            );
          }
          break;

        case "yolo_discover_done":
          this.addFeedItem(
            `YOLO discovered ${event.data.new} new / ${event.data.total} total`,
            "#22d3ee",
          );
          this.fetchJobs();
          this.fetchStats();
          this.fetchCompanies();
          break;

        case "yolo_score_done":
          this.addFeedItem(`YOLO scored ${event.data.scored} jobs`, "#8b5cf6");
          this.fetchJobs();
          this.fetchStats();
          this.updateCharts();
          break;

        case "yolo_applying":
          this.addFeedItem(
            `YOLO [${event.data.current}/${event.data.total}] ${event.data.title} @ ${event.data.company}`,
            "#10b981",
          );
          break;

        case "yolo_apply_done":
          this.addFeedItem(
            `YOLO applied: ${event.data.applied} jobs ${event.data.dry_run ? "(dry run)" : "(submitted)"}`,
            "#10b981",
          );
          this.fetchJobs();
          this.fetchStats();
          this.updateCharts();
          break;

        case "yolo_cycle_complete":
          this.addFeedItem(
            `YOLO CYCLE ${event.data.cycle} COMPLETE`,
            "#fbbf24",
          );
          this.fetchYoloLog();
          break;

        case "yolo_waiting":
          this.yoloState.phase = "waiting";
          this.addFeedItem(
            `YOLO sleeping ${event.data.minutes}min until cycle ${event.data.next_cycle}`,
            "#f59e0b",
          );
          break;

        case "yolo_cancelled":
          this.yoloState.running = false;
          this.yoloState.phase = null;
          this.notify("YOLO mode cancelled.", "warning");
          this.addFeedItem("YOLO CANCELLED", "#f43f5e");
          this.fetchYoloLog();
          break;

        case "yolo_error":
          this.addFeedItem(
            `YOLO ERROR [${event.data.phase}]: ${event.data.error}`,
            "#f43f5e",
          );
          if (event.data.phase === "fatal") {
            this.yoloState.running = false;
            this.yoloState.phase = null;
          }
          break;
      }
    },

    // ===================================================================
    // VIEW ROUTING
    // ===================================================================

    switchView(view) {
      this.currentView = view;
      if (view === "profile") {
        this.loadFullProfile();
        this.loadResumes();
      }
      if (view === "dashboard") {
        this.$nextTick(() => this.updateCharts());
      }
    },

    // ===================================================================
    // FULL PROFILE EDITOR
    // ===================================================================

    async loadFullProfile() {
      try {
        const res = await fetch("/api/profile");
        this.fullProfile = await res.json();
        this.coverLetterTemplates =
          this.fullProfile.cover_letter_templates || [];
      } catch (err) {
        this.notify("Failed to load profile", "error");
      }
    },

    updateProfileField(path, value) {
      const keys = path.split(".");
      let obj = this.fullProfile;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!obj[keys[i]]) obj[keys[i]] = {};
        obj = obj[keys[i]];
      }
      obj[keys[keys.length - 1]] = value;
      this.scheduleProfileSave();
    },

    getProfileField(path) {
      const keys = path.split(".");
      let obj = this.fullProfile;
      for (const k of keys) {
        if (!obj || typeof obj !== "object") return "";
        obj = obj[k];
      }
      return obj || "";
    },

    scheduleProfileSave() {
      if (this.profileSaveTimer) clearTimeout(this.profileSaveTimer);
      this.profileSaveTimer = setTimeout(() => this.saveFullProfile(), 800);
    },

    async saveFullProfile() {
      this.profileSaving = true;
      try {
        await fetch("/api/profile", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.fullProfile),
        });
        this.lastSaveTime = new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        });
        this.fetchProfile(); // sync sidebar quick-edit
      } catch (err) {
        this.notify("Profile save failed: " + err.message, "error");
      } finally {
        this.profileSaving = false;
      }
    },

    toggleSection(section) {
      this.profileSections[section] = !this.profileSections[section];
    },

    // Profile array field helpers (for tag-chip lists in profile editor)
    addProfileArrayItem(path, inputRef) {
      const val = this[inputRef]?.trim();
      if (!val) return;
      const keys = path.split(".");
      let obj = this.fullProfile;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!obj[keys[i]]) obj[keys[i]] = {};
        obj = obj[keys[i]];
      }
      const lastKey = keys[keys.length - 1];
      if (!Array.isArray(obj[lastKey])) obj[lastKey] = [];
      if (!obj[lastKey].includes(val)) {
        obj[lastKey].push(val);
        this.scheduleProfileSave();
      }
      this[inputRef] = "";
    },

    removeProfileArrayItem(path, index) {
      const keys = path.split(".");
      let obj = this.fullProfile;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!obj[keys[i]]) return;
        obj = obj[keys[i]];
      }
      const arr = obj[keys[keys.length - 1]];
      if (Array.isArray(arr)) {
        arr.splice(index, 1);
        this.scheduleProfileSave();
      }
    },

    getProfileArray(path) {
      const keys = path.split(".");
      let obj = this.fullProfile;
      for (const k of keys) {
        if (!obj || typeof obj !== "object") return [];
        obj = obj[k];
      }
      return Array.isArray(obj) ? obj : [];
    },

    // ===================================================================
    // RESUME MANAGEMENT
    // ===================================================================

    async loadResumes() {
      try {
        const res = await fetch("/api/resumes");
        this.resumes = await res.json();
      } catch (_) {}
    },

    async uploadResume(fileInput) {
      const file = fileInput?.files?.[0];
      if (!file || !this.newResumeName.trim()) {
        this.notify("Provide a name and select a file.", "warning");
        return;
      }
      this.resumeUploading = true;
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("name", this.newResumeName.trim());
        const res = await fetch("/api/resumes", { method: "POST", body: fd });
        if (!res.ok) throw new Error(await res.text());
        this.newResumeName = "";
        fileInput.value = "";
        await this.loadResumes();
        this.notify("Resume uploaded.", "success");
        this.addFeedItem("Resume uploaded: " + this.newResumeName, "#3b82f6");
      } catch (err) {
        this.notify("Upload failed: " + err.message, "error");
      } finally {
        this.resumeUploading = false;
      }
    },

    async setDefaultResume(name) {
      await fetch(`/api/resumes/${encodeURIComponent(name)}/default`, {
        method: "PATCH",
      });
      await this.loadResumes();
      this.notify(`"${name}" set as default.`, "success");
    },

    async deleteResume(name) {
      if (!confirm(`Delete resume "${name}"?`)) return;
      await fetch(`/api/resumes/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      await this.loadResumes();
      this.notify("Resume deleted.", "success");
    },

    handleResumeDrop(e) {
      e.preventDefault();
      this.resumeDragActive = false;
      const file = e.dataTransfer?.files?.[0];
      if (file && this.$refs.resumeFileInput) {
        const dt = new DataTransfer();
        dt.items.add(file);
        this.$refs.resumeFileInput.files = dt.files;
      }
    },

    // ===================================================================
    // COVER LETTER TEMPLATES
    // ===================================================================

    addCoverLetterTemplate() {
      if (!this.newTemplateName.trim() || !this.newTemplateBody.trim()) return;
      if (!this.fullProfile.cover_letter_templates) {
        this.fullProfile.cover_letter_templates = [];
      }
      this.fullProfile.cover_letter_templates.push({
        name: this.newTemplateName.trim(),
        body: this.newTemplateBody.trim(),
      });
      this.coverLetterTemplates = this.fullProfile.cover_letter_templates;
      this.newTemplateName = "";
      this.newTemplateBody = "";
      this.scheduleProfileSave();
    },

    removeCoverLetterTemplate(index) {
      this.fullProfile.cover_letter_templates.splice(index, 1);
      this.coverLetterTemplates = this.fullProfile.cover_letter_templates;
      this.scheduleProfileSave();
    },

    updateCoverLetterTemplate(index, field, value) {
      this.fullProfile.cover_letter_templates[index][field] = value;
      this.scheduleProfileSave();
    },

    // ===================================================================
    // CHARTS
    // ===================================================================

    async initCharts() {
      // Destroy existing charts first
      if (this.scoreChart) {
        this.scoreChart.destroy();
        this.scoreChart = null;
      }
      if (this.timelineChart) {
        this.timelineChart.destroy();
        this.timelineChart = null;
      }

      // Set Chart.js defaults for dark theme
      Chart.defaults.color = "#64748b";
      Chart.defaults.borderColor = "#1e293b";
      Chart.defaults.font.family = "'JetBrains Mono', monospace";
      Chart.defaults.font.size = 10;

      await Promise.all([this._buildScoreChart(), this._buildTimelineChart()]);
    },

    async updateCharts() {
      if (this.scoreChart) {
        this.scoreChart.destroy();
        this.scoreChart = null;
      }
      if (this.timelineChart) {
        this.timelineChart.destroy();
        this.timelineChart = null;
      }
      await this.initCharts();
    },

    async _buildScoreChart() {
      const canvas = document.getElementById("scoreChart");
      if (!canvas) return;
      // Destroy any existing chart on this canvas
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();

      const res = await fetch("/api/stats/scores");
      const scores = await res.json();

      const bgColors = scores.map((s) => {
        if (s.bracket === "90-100") return "rgba(16, 185, 129, 0.6)";
        if (s.bracket === "80-89") return "rgba(16, 185, 129, 0.4)";
        if (s.bracket === "70-79") return "rgba(245, 158, 11, 0.5)";
        if (s.bracket === "60-69") return "rgba(245, 158, 11, 0.3)";
        if (s.bracket === "50-59") return "rgba(244, 63, 94, 0.4)";
        return "rgba(100, 116, 139, 0.3)";
      });

      const borderColors = scores.map((s) => {
        if (s.bracket === "90-100") return "#10b981";
        if (s.bracket === "80-89") return "#10b981";
        if (s.bracket === "70-79") return "#f59e0b";
        if (s.bracket === "60-69") return "#f59e0b";
        if (s.bracket === "50-59") return "#f43f5e";
        return "#475569";
      });

      this.scoreChart = new Chart(canvas, {
        type: "bar",
        data: {
          labels: scores.map((s) => s.bracket),
          datasets: [
            {
              label: "Jobs",
              data: scores.map((s) => s.count),
              backgroundColor: bgColors,
              borderColor: borderColors,
              borderWidth: 1,
              borderRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "#1a2235",
              borderColor: "#2a3a52",
              borderWidth: 1,
              titleColor: "#e2e8f0",
              bodyColor: "#94a3b8",
              callbacks: { label: (ctx) => ` ${ctx.parsed.y} jobs` },
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: { precision: 0 },
              grid: { color: "rgba(30,41,59,0.5)" },
            },
            x: { grid: { display: false } },
          },
        },
      });
    },

    async _buildTimelineChart() {
      const canvas = document.getElementById("timelineChart");
      if (!canvas) return;
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();

      const res = await fetch("/api/stats/timeline");
      const timeline = await res.json();
      timeline.reverse();

      this.timelineChart = new Chart(canvas, {
        type: "line",
        data: {
          labels: timeline.map((t) => t.date),
          datasets: [
            {
              label: "Discovered",
              data: timeline.map((t) => t.total),
              borderColor: "#22d3ee",
              backgroundColor: "rgba(34,211,238,0.08)",
              fill: true,
              tension: 0.4,
              pointRadius: 2,
              pointBackgroundColor: "#22d3ee",
              borderWidth: 1.5,
            },
            {
              label: "Applied",
              data: timeline.map((t) => t.applied),
              borderColor: "#10b981",
              backgroundColor: "rgba(16,185,129,0.08)",
              fill: true,
              tension: 0.4,
              pointRadius: 2,
              pointBackgroundColor: "#10b981",
              borderWidth: 1.5,
            },
          ],
        },
        options: {
          responsive: true,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: {
              labels: {
                font: { size: 10 },
                usePointStyle: true,
                pointStyleWidth: 8,
                padding: 16,
              },
            },
            tooltip: {
              backgroundColor: "#1a2235",
              borderColor: "#2a3a52",
              borderWidth: 1,
              titleColor: "#e2e8f0",
              bodyColor: "#94a3b8",
            },
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: { precision: 0 },
              grid: { color: "rgba(30,41,59,0.5)" },
            },
            x: {
              ticks: { maxTicksLimit: 8 },
              grid: { display: false },
            },
          },
        },
      });
    },
  };
}
