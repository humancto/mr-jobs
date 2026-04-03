/**
 * MR.Jobs — Job Intelligence Dashboard
 *
 * Alpine.js application with:
 * - Real-time WebSocket event feed
 * - Profile/preferences inline editing
 * - Scheduler countdown timers
 * - Dark-themed Chart.js visualizations
 * - Full job CRUD with expandable detail rows
 */

function mrjobs() {
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
    _wsReconnectDelay: 1000,
    _wsReconnecting: false,

    // ----- Activity Feed -----
    activityFeed: [],
    maxFeedItems: 50,

    // ----- Scheduler -----
    schedulerData: { running: false, jobs: [], last_results: {} },
    _schedulerTimer: null,
    _countdownTimer: null,

    // ----- Profile Editing -----
    _sidebarSaveTimer: null,
    sidebarSaving: false,
    sidebarSaved: false,
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
    newKeyword: "",
    newPrimarySkill: "",
    newSecondarySkill: "",
    newFavCompany: "",
    newSearchQuery: "",

    // ----- View Routing -----
    currentView: "dashboard", // 'dashboard' | 'profile'

    // ----- Apply State -----
    applyState: {
      running: false,
      job_id: null,
      progress: [],
      total: 0,
    },

    // ----- Follow-ups -----
    followUps: { overdue: [], ghosts: [] },

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

    // ----- Profile AI Analysis -----
    profileScoring: false,
    profileInsights: null,

    // ----- Resume Tailoring -----
    tailorData: {},
    tailoring: {},

    // ----- Cover Letter Templates -----
    coverLetterTemplates: [],
    newTemplateName: "",
    newTemplateBody: "",

    // ----- Interview Practice -----
    interviewModal: false,
    interviewSession: null, // { session_id, job_title, company, type, provider, mode }
    interviewMessages: [], // [{ role: 'interviewer'|'candidate', text }]
    interviewInput: "",
    interviewSending: false,
    interviewEnded: false,
    interviewEvaluating: false,
    interviewEvaluation: null, // formatted evaluation summary
    interviewHistory: [], // past sessions from DB
    interviewHistoryOpen: false,
    interviewConfig: {
      role: "Software Engineer",
      company: "Tech Company",
      type: "mixed",
      difficulty: "mid",
      duration: 30,
      provider: "gemini",
      mode: "text", // text | voice | video
    },
    availableProviders: {}, // { gemini: { available, label }, openai: { ... } }

    // ----- Voice/Video Interview -----
    interviewAudioWs: null, // WebSocket for audio streaming
    interviewMicActive: false, // mic recording toggle
    interviewMicStream: null, // MediaStream from getUserMedia
    interviewAudioContext: null, // AudioContext for processing
    geminiAudioNextTime: 0, // scheduled time for next audio chunk playback
    interviewVoiceStatus: "idle", // idle | listening | thinking | speaking
    interviewVideoStream: null, // MediaStream for webcam
    interviewMediaRecorder: null, // MediaRecorder for session recording
    interviewRecordedChunks: [], // WebM blobs
    interviewWebcamActive: false,
    interviewFrameTimer: null, // setInterval for frame capture

    // ----- Gemini Live Streaming -----
    geminiLiveWs: null, // WebSocket for Gemini Live real-time streaming
    geminiLiveActive: false, // whether Gemini Live mode is currently active

    // ----- OpenAI Realtime Streaming -----
    openaiLiveWs: null, // WebSocket for OpenAI Realtime audio streaming
    openaiLiveActive: false, // whether OpenAI Realtime mode is currently active

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

    // ----- Selection & Purge -----
    selectedJobs: [],

    // ----- Charts -----
    scoreChart: null,
    timelineChart: null,
    _radarChart: null,

    // ----- Setup Wizard -----
    needsSetup: false,
    wizardStep: 1,
    wizardData: {
      personal: {
        first_name: "",
        last_name: "",
        email: "",
        phone: "",
        location: "",
      },
      preferences: { roles: [], min_match_score: 65, locations: ["Remote"] },
      skills: { primary: [] },
      search: { enabled: true, queries: [] },
    },
    wizardNewRole: "",
    wizardNewLocation: "",
    wizardNewSkill: "",
    wizardNewQuery: "",

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
      const total = Object.values(s).reduce(
        (sum, v) => sum + (typeof v === "number" ? v : 0),
        0,
      );
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
      // Check if first-run setup is needed
      try {
        const res = await fetch("/api/profile");
        const data = await res.json();
        if (data.needs_setup) {
          this.needsSetup = true;
          return; // Don't load dashboard data yet
        }
      } catch (e) {
        // Server might not be ready yet
      }

      await Promise.all([
        this.fetchJobs(),
        this.fetchStats(),
        this.fetchCompanies(),
        this.fetchStatuses(),
        this.fetchProfile(),
        this.fetchSchedulerStatus(),
        this.fetchFollowUps(),
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
    // SETUP WIZARD
    // ===================================================================

    wizardNext() {
      if (this.wizardStep < 4) this.wizardStep++;
    },

    wizardBack() {
      if (this.wizardStep > 1) this.wizardStep--;
    },

    wizardAddRole() {
      const val = this.wizardNewRole?.trim();
      if (val && !this.wizardData.preferences.roles.includes(val)) {
        this.wizardData.preferences.roles.push(val);
      }
      this.wizardNewRole = "";
    },

    wizardRemoveRole(i) {
      this.wizardData.preferences.roles.splice(i, 1);
    },

    wizardAddLocation() {
      const val = this.wizardNewLocation?.trim();
      if (val && !this.wizardData.preferences.locations.includes(val)) {
        this.wizardData.preferences.locations.push(val);
      }
      this.wizardNewLocation = "";
    },

    wizardRemoveLocation(i) {
      this.wizardData.preferences.locations.splice(i, 1);
    },

    wizardAddSkill() {
      const val = this.wizardNewSkill?.trim();
      if (val && !this.wizardData.skills.primary.includes(val)) {
        this.wizardData.skills.primary.push(val);
      }
      this.wizardNewSkill = "";
    },

    wizardRemoveSkill(i) {
      this.wizardData.skills.primary.splice(i, 1);
    },

    wizardAddQuery() {
      const val = this.wizardNewQuery?.trim();
      if (val && !this.wizardData.search.queries.includes(val)) {
        this.wizardData.search.queries.push(val);
      }
      this.wizardNewQuery = "";
    },

    wizardRemoveQuery(i) {
      this.wizardData.search.queries.splice(i, 1);
    },

    async wizardSubmit() {
      try {
        const res = await fetch("/api/setup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.wizardData),
        });
        if (!res.ok) throw new Error("Setup failed");
        this.needsSetup = false;
        // Re-initialize the dashboard
        await this.init();
      } catch (err) {
        alert("Setup failed: " + err.message);
      }
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
        this.debouncedSaveProfile();
      }
      this[inputField] = "";
    },

    removeItem(field, index) {
      this.profileEdit[field].splice(index, 1);
      this.profileDirty = true;
      this.debouncedSaveProfile();
    },

    debouncedSaveProfile() {
      if (this._sidebarSaveTimer) clearTimeout(this._sidebarSaveTimer);
      this._sidebarSaveTimer = setTimeout(() => this.saveProfile(), 1500);
    },

    async saveProfile() {
      this.sidebarSaving = true;
      this.sidebarSaved = false;
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
        this.sidebarSaved = true;
        this.addFeedItem("Profile configuration saved.", "#10b981");
        // Clear "Saved" indicator after 3s
        setTimeout(() => {
          this.sidebarSaved = false;
        }, 3000);
      } catch (err) {
        this.notify(`Profile save failed: ${err.message}`, "error");
      } finally {
        this.sidebarSaving = false;
      }
    },

    // ===================================================================
    // ACTIONS
    // ===================================================================

    async discover() {
      this.discovering = true;
      setTimeout(() => {
        this.discovering = false;
      }, 120000);
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
      setTimeout(() => {
        this.scoring = false;
      }, 120000);
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

    // ===================================================================
    // FOLLOW-UPS & GHOST DETECTION
    // ===================================================================

    async fetchFollowUps() {
      try {
        const res = await fetch("/api/follow-ups");
        this.followUps = await res.json();
      } catch (_) {}
    },

    async markFollowUpDone(jobId, nextDays = 7) {
      try {
        await fetch(`/api/jobs/${jobId}/follow-up`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ next_days: nextDays }),
        });
        this.notify("Follow-up marked done, next scheduled.", "success");
        this.fetchFollowUps();
        this.fetchJobs();
      } catch (err) {
        this.notify(`Follow-up error: ${err.message}`, "error");
      }
    },

    async dismissFollowUp(jobId) {
      try {
        await fetch(`/api/jobs/${jobId}/dismiss-follow-up`, { method: "POST" });
        this.notify("Follow-up dismissed.", "info");
        this.fetchFollowUps();
        this.fetchJobs();
      } catch (err) {
        this.notify(`Dismiss error: ${err.message}`, "error");
      }
    },

    isOverdue(job) {
      return this.followUps.overdue?.some((f) => f.id === job.id);
    },

    isGhost(job) {
      return this.followUps.ghosts?.some((f) => f.id === job.id);
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

    async tailorJob(jobId) {
      this.tailoring[jobId] = true;
      this.notify("Generating tailored resume content...", "info");
      try {
        await fetch(`/api/jobs/${jobId}/tailor`, { method: "POST" });
      } catch (err) {
        this.tailoring[jobId] = false;
        this.notify(`Tailoring failed: ${err.message}`, "error");
      }
    },

    async fetchTailorData(jobId) {
      try {
        const res = await fetch(`/api/jobs/${jobId}/tailor`);
        const data = await res.json();
        if (data && (data.tailored_summary || data.tailored_bullets?.length)) {
          this.tailorData[jobId] = data;
        }
      } catch (_) {}
    },

    // ===================================================================
    // SELECTION, IGNORE & PURGE
    // ===================================================================

    toggleSelectJob(jobId) {
      const idx = this.selectedJobs.indexOf(jobId);
      if (idx >= 0) {
        this.selectedJobs.splice(idx, 1);
      } else {
        this.selectedJobs.push(jobId);
      }
    },

    toggleSelectAll(event) {
      if (event.target.checked) {
        this.selectedJobs = this.jobs.map((j) => j.id);
      } else {
        this.selectedJobs = [];
      }
    },

    async ignoreSelected() {
      const count = this.selectedJobs.length;
      if (!count) return;
      if (
        !confirm(
          `Mark ${count} job(s) as IGNORED?\n\nThey will be excluded from future discovery runs.`,
        )
      )
        return;
      try {
        const res = await fetch("/api/jobs/ignore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_ids: this.selectedJobs }),
        });
        const data = await res.json();
        this.notify(
          `${data.ignored} job(s) ignored. They won't reappear.`,
          "success",
        );
        this.selectedJobs = [];
        this.fetchJobs();
        this.fetchStats();
      } catch (err) {
        this.notify(`Ignore failed: ${err.message}`, "error");
      }
    },

    async confirmPurge() {
      const choice = prompt(
        "PURGE ALL DISCOVERY DATA\n\n" +
          "Type 'purge' to delete all jobs (keep ignore list)\n" +
          "Type 'nuke' to delete everything including ignore list\n" +
          "Press Cancel to abort",
      );
      if (!choice) return;
      const keepIgnores = choice.trim().toLowerCase() !== "nuke";
      if (
        choice.trim().toLowerCase() !== "purge" &&
        choice.trim().toLowerCase() !== "nuke"
      ) {
        this.notify("Aborted — type 'purge' or 'nuke' to confirm.", "warning");
        return;
      }
      try {
        const res = await fetch("/api/purge", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keep_ignore_list: keepIgnores }),
        });
        const data = await res.json();
        this.notify(
          `Purged ${data.jobs_deleted} jobs.` +
            (data.ignores_cleared
              ? ` Cleared ${data.ignores_cleared} ignore entries.`
              : " Ignore list preserved."),
          "success",
        );
        this.selectedJobs = [];
        this.fetchJobs();
        this.fetchStats();
        this.initCharts();
      } catch (err) {
        this.notify(`Purge failed: ${err.message}`, "error");
      }
    },

    // ===================================================================
    // APPLY
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
        : "Start LIVE APPLY?\n\nThis will ACTUALLY SUBMIT applications for all matched jobs. Are you sure?";

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
      if (this.expandedJob === jobId) {
        this.fetchTailorData(jobId);
      }
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
      if (this._wsReconnecting) return;
      this._wsReconnecting = true;

      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      this.ws = new WebSocket(`${proto}//${location.host}/ws`);

      this.ws.onopen = () => {
        this.wsConnected = true;
        this._wsReconnectDelay = 1000;
        this._wsReconnecting = false;
        this.addFeedItem("WebSocket link established.", "#10b981");
        // If we reconnected during an active interview, poll to recover state
        if (this.interviewSession && !this.interviewEnded) {
          this._pollInterviewState();
        }
      };

      this.ws.onclose = () => {
        this.wsConnected = false;
        this._wsReconnecting = false;
        // Exponential backoff: 1s, 2s, 4s, 8s, max 15s
        const delay = this._wsReconnectDelay || 1000;
        this._wsReconnectDelay = Math.min(delay * 2, 15000);
        this.addFeedItem(
          `WebSocket disconnected. Reconnecting in ${Math.round(delay / 1000)}s...`,
          "#f43f5e",
        );
        setTimeout(() => this.connectWebSocket(), delay);
      };

      this.ws.onerror = () => {
        this.wsConnected = false;
        this._wsReconnecting = false;
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

        case "tailor_complete":
          this.tailoring[event.data.id] = false;
          this.notify("Resume tailoring complete!", "success");
          this.addFeedItem(
            `Tailored resume for ${event.data.id?.substring(0, 8)}`,
            "#8b5cf6",
          );
          this.fetchTailorData(event.data.id);
          break;

        case "tailor_error":
          this.tailoring[event.data.id] = false;
          this.notify(`Tailoring error: ${event.data.error}`, "error");
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

        // ----- Apply Events -----
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

        // ----- Profile AI Analysis -----
        case "profile_score_complete":
          this.profileScoring = false;
          this.profileInsights = event.data;
          this.notify(
            `Profile score: ${event.data.profile_score}/100`,
            "success",
          );
          this.addFeedItem(
            `Profile analyzed: ${event.data.profile_score}/100`,
            "#a78bfa",
          );
          break;

        case "profile_score_error":
          this.profileScoring = false;
          this.notify(`Profile analysis failed: ${event.data.error}`, "error");
          this.addFeedItem(
            `Profile analysis error: ${event.data.error}`,
            "#f43f5e",
          );
          break;

        case "profile_score_started":
          this.addFeedItem("AI analyzing profile + resume...", "#a78bfa");
          break;

        // ----- Follow-up Events -----
        case "follow_up_set":
        case "follow_up_done":
          this.fetchFollowUps();
          break;

        // ----- Interview Events -----
        case "interview_started":
          this.interviewSending = false;
          this.interviewMessages.push({
            role: "interviewer",
            text: event.data.opening,
          });
          this.addFeedItem(
            `Interview started: ${event.data.job_title} @ ${event.data.company}`,
            "#8b5cf6",
          );
          this.$nextTick(() => this.scrollInterviewChat());
          break;

        case "interview_response":
          this.interviewSending = false;
          this.interviewMessages.push({
            role: "interviewer",
            text: event.data.response,
          });
          if (event.data.should_end) {
            this.interviewEnded = true;
          }
          this.$nextTick(() => this.scrollInterviewChat());
          break;

        case "interview_evaluating":
          this.interviewEvaluating = true;
          this.addFeedItem("AI evaluating interview performance...", "#a78bfa");
          break;

        case "interview_complete":
          this.interviewEvaluating = false;
          this.interviewEnded = true;
          if (
            event.data.evaluation &&
            event.data.evaluation.has_evaluation !== undefined
          ) {
            this.interviewEvaluation = event.data.evaluation;
          } else if (event.data.evaluation) {
            this.interviewEvaluation = this._buildEvalObj(
              event.data.evaluation,
            );
          }
          this.notify("Interview evaluation complete!", "success");
          this.addFeedItem(
            `Interview score: ${event.data.evaluation?.overall_score || "?"}/5`,
            "#10b981",
          );
          this.$nextTick(() => {
            this.scrollInterviewChat();
            this.renderRadarChart();
          });
          break;

        case "interview_error":
          this.interviewSending = false;
          this.interviewEvaluating = false;
          this.notify(`Interview error: ${event.data.error}`, "error");
          this.addFeedItem(`Interview error: ${event.data.error}`, "#f43f5e");
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

    async scoreProfile() {
      this.profileScoring = true;
      try {
        const res = await fetch("/api/profile/score", { method: "POST" });
        if (res.ok) {
          this.notify(
            "Profile analysis started — Claude is reading your resume...",
            "success",
          );
          this.addFeedItem("Profile AI analysis started", "#a78bfa");
        } else {
          const data = await res.json();
          this.notify(data.detail || "Profile analysis failed", "error");
          this.profileScoring = false;
        }
      } catch (err) {
        this.notify(`Profile analysis error: ${err.message}`, "error");
        this.profileScoring = false;
      }
    },

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

    // ===================================================================
    // INTERVIEW PRACTICE
    // ===================================================================

    async fetchProviders() {
      try {
        const res = await fetch("/api/providers");
        const data = await res.json();
        this.availableProviders = data.providers || {};
        // Set smart default: prefer Gemini (live streaming), then OpenAI
        if (this.availableProviders.gemini?.available) {
          this.interviewConfig.provider = "gemini";
        } else if (this.availableProviders.openai?.available) {
          this.interviewConfig.provider = "openai";
        }
      } catch {
        this.availableProviders = {};
      }
    },

    openInterviewModal(jobId = null) {
      // Reset state
      this.interviewMessages = [];
      this.interviewInput = "";
      this.interviewSending = false;
      this.interviewEnded = false;
      this.interviewEvaluating = false;
      this.interviewEvaluation = null;
      this.interviewSession = null;
      this.interviewMicActive = false;
      this.interviewWebcamActive = false;
      this.interviewVoiceStatus = "idle";
      this.interviewRecordedChunks = [];
      if (this.geminiLiveActive) {
        this.stopGeminiLiveMode();
      }
      if (this.openaiLiveActive) {
        this.stopOpenAILiveMode();
      }
      this.geminiLiveWs = null;
      this.geminiLiveActive = false;
      this.openaiLiveWs = null;
      this.openaiLiveActive = false;

      // If job context provided, pre-fill config
      if (jobId) {
        const job = this.jobs.find((j) => j.id === jobId);
        if (job) {
          this.interviewConfig.role = job.title || "Software Engineer";
          this.interviewConfig.company = job.company || "Tech Company";
        }
      }

      this.fetchProviders();
      this.interviewModal = true;
    },

    closeInterviewModal() {
      this.interviewModal = false;
      this.stopInterviewMedia();
      if (this.interviewSession && !this.interviewEnded) {
        fetch("/api/interview/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: this.interviewSession.session_id,
          }),
        }).catch(() => {});
      }
    },

    stopInterviewMedia() {
      // Stop Gemini Live mode if active
      if (this.geminiLiveActive) {
        this.stopGeminiLiveMode();
      }
      // Stop OpenAI Realtime mode if active
      if (this.openaiLiveActive) {
        this.stopOpenAILiveMode();
      }
      // Stop mic
      if (this.interviewMicStream) {
        this.interviewMicStream.getTracks().forEach((t) => t.stop());
        this.interviewMicStream = null;
      }
      // Stop webcam
      if (this.interviewVideoStream) {
        this.interviewVideoStream.getTracks().forEach((t) => t.stop());
        this.interviewVideoStream = null;
      }
      // Stop media recorder
      if (
        this.interviewMediaRecorder &&
        this.interviewMediaRecorder.state !== "inactive"
      ) {
        this.interviewMediaRecorder.stop();
      }
      // Stop frame capture
      if (this.interviewFrameTimer) {
        clearInterval(this.interviewFrameTimer);
        this.interviewFrameTimer = null;
      }
      // Close audio WS
      if (this.interviewAudioWs) {
        this.interviewAudioWs.close();
        this.interviewAudioWs = null;
      }
      // Close audio context
      if (this.interviewAudioContext) {
        this.interviewAudioContext.close().catch(() => {});
        this.interviewAudioContext = null;
      }
      this.interviewMicActive = false;
      this.interviewWebcamActive = false;
    },

    async startInterview(jobId = null) {
      this.interviewMessages = [];
      this.interviewEnded = false;
      this.interviewEvaluation = null;
      this.interviewSending = true;

      // Voice requires Gemini or OpenAI; Video requires Gemini only
      let effectiveMode = this.interviewConfig.mode;
      if (
        effectiveMode === "video" &&
        this.interviewConfig.provider !== "gemini"
      ) {
        effectiveMode = "voice"; // OpenAI can do voice but not video
        this.interviewConfig.mode = "voice";
        this.notify(
          "Video mode requires Gemini. Using voice mode instead.",
          "warning",
        );
      }
      if (
        (effectiveMode === "voice" || effectiveMode === "video") &&
        !["gemini", "openai"].includes(this.interviewConfig.provider)
      ) {
        effectiveMode = "text";
        this.interviewConfig.mode = "text";
        this.notify(
          "Live audio requires Gemini or OpenAI. Falling back to text mode.",
          "warning",
        );
      }

      const payload = {
        role: this.interviewConfig.role,
        company: this.interviewConfig.company,
        type: this.interviewConfig.type,
        difficulty: this.interviewConfig.difficulty,
        duration: this.interviewConfig.duration,
        provider: this.interviewConfig.provider,
        mode: effectiveMode,
      };
      if (jobId) payload.job_id = jobId;

      try {
        const res = await fetch("/api/interview/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        this.interviewSession = {
          session_id: data.session_id,
          job_title: payload.role,
          company: payload.company,
          type: payload.type,
          provider: payload.provider,
          mode: payload.mode,
        };

        // Start voice/video modes after session is created
        if (payload.mode === "voice" || payload.mode === "video") {
          if (payload.provider === "gemini") {
            // Gemini Live: real-time streaming (voice + video)
            await this.startGeminiLiveMode(
              data.session_id,
              payload.mode === "video",
            );
          } else if (payload.provider === "openai") {
            // OpenAI Realtime: live audio phone call (voice only)
            await this.startOpenAILiveMode(data.session_id);
          }
        }

        this.addFeedItem(
          `Interview started: ${payload.role} @ ${payload.company}`,
          "#8b5cf6",
        );
      } catch (err) {
        this.notify(`Interview start failed: ${err.message}`, "error");
        this.interviewSending = false;
      }
    },

    async sendInterviewResponse() {
      const text = this.interviewInput.trim();
      if (!text || !this.interviewSession || this.interviewSending) return;

      this.interviewMessages.push({ role: "candidate", text });
      this.interviewInput = "";
      this.interviewSending = true;

      try {
        await fetch("/api/interview/respond", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: this.interviewSession.session_id,
            text,
          }),
        });
      } catch (err) {
        this.notify(`Response failed: ${err.message}`, "error");
        this.interviewSending = false;
      }

      // Scroll chat to bottom
      this.$nextTick(() => this.scrollInterviewChat());
    },

    async endInterview() {
      if (!this.interviewSession) return;
      this.interviewEvaluating = true;

      // Stop media streams
      this.stopInterviewMedia();

      // Upload recording if we have one
      if (this.interviewRecordedChunks.length > 0) {
        await this.uploadRecording(this.interviewSession.session_id);
      }

      try {
        await fetch("/api/interview/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: this.interviewSession.session_id,
          }),
        });
      } catch (err) {
        this.notify(`End interview failed: ${err.message}`, "error");
        this.interviewEvaluating = false;
      }
    },

    scrollInterviewChat() {
      const chat = document.getElementById("interview-chat");
      if (chat) chat.scrollTop = chat.scrollHeight;
    },

    async _pollInterviewState() {
      // Polling fallback: recover interview state after WebSocket reconnection
      if (!this.interviewSession) return;
      const sid = this.interviewSession.session_id;
      try {
        const res = await fetch(`/api/interview/state/${sid}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!data.found) return;

        // Sync transcript — only add messages we don't already have
        const serverMessages = (data.transcript || []).map((t) => ({
          role: t.role === "interviewer" ? "interviewer" : "candidate",
          text: t.text,
        }));
        if (serverMessages.length > this.interviewMessages.length) {
          this.interviewMessages = serverMessages;
          this.$nextTick(() => this.scrollInterviewChat());
        }

        // If evaluation arrived while we were disconnected
        if (data.evaluation && !data.evaluation.error) {
          this.interviewEvaluating = false;
          this.interviewEnded = true;
          // Re-format if needed (server may return raw evaluation)
          if (data.evaluation.has_evaluation !== undefined) {
            this.interviewEvaluation = data.evaluation;
          } else {
            const e = data.evaluation;
            this.interviewEvaluation = this._buildEvalObj(e);
          }
        }

        // Update sending state
        if (data.state === "active") {
          this.interviewSending = false;
        } else if (data.state === "ended" || data.state === "evaluated") {
          this.interviewSending = false;
          this.interviewEnded = true;
        }
      } catch (_) {
        // Polling is best-effort
      }
    },

    async fetchInterviewHistory() {
      try {
        const res = await fetch("/api/interview/sessions");
        this.interviewHistory = await res.json();
      } catch (err) {
        this.interviewHistory = [];
      }
    },

    async viewPastInterview(sessionId) {
      try {
        const res = await fetch(`/api/interview/sessions/${sessionId}`);
        const data = await res.json();

        // Load into modal as read-only
        this.interviewMessages = (data.transcript || []).map((t) => ({
          role: t.role === "interviewer" ? "interviewer" : "candidate",
          text: t.text,
        }));
        this.interviewEnded = true;
        this.interviewEvaluating = false;
        this.interviewSession = {
          session_id: data.session_id,
          job_title: data.job_title,
          company: data.company,
          type: data.interview_type,
          provider: data.provider || "",
          mode: data.mode || "text",
        };

        // Format evaluation
        if (data.evaluation && !data.evaluation.error) {
          this.interviewEvaluation = this._buildEvalObj(data.evaluation);
        } else {
          this.interviewEvaluation = null;
        }

        this.interviewHistoryOpen = false;
        this.interviewModal = true;
        this.$nextTick(() => this.renderRadarChart());
      } catch (err) {
        this.notify(`Failed to load interview: ${err.message}`, "error");
      }
    },

    getRecLabel(rec) {
      return (
        {
          strong_hire: "STRONG HIRE",
          hire: "HIRE",
          lean_hire: "LEAN HIRE",
          lean_no: "LEAN NO",
          no_hire: "NO HIRE",
        }[rec] ||
        rec?.toUpperCase() ||
        "N/A"
      );
    },

    getRecColor(rec) {
      return (
        {
          strong_hire: "#10b981",
          hire: "#22d3ee",
          lean_hire: "#f59e0b",
          lean_no: "#f97316",
          no_hire: "#f43f5e",
        }[rec] || "#94a3b8"
      );
    },

    getDimName(key) {
      return (
        {
          communication: "Communication",
          technical_depth: "Technical Depth",
          problem_solving: "Problem Solving",
          leadership: "Leadership",
          tone_and_delivery: "Tone & Delivery",
          engagement: "Engagement",
        }[key] ||
        key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      );
    },

    _buildEvalObj(e) {
      return {
        has_evaluation: true,
        overall_score: e.overall_score || 0,
        recommendation: e.recommendation || "",
        recommendation_label:
          {
            strong_hire: "Strong Hire",
            hire: "Hire",
            lean_hire: "Lean Hire",
            lean_no: "Lean No",
            no_hire: "No Hire",
          }[e.recommendation] || e.recommendation,
        recommendation_color:
          {
            strong_hire: "#10b981",
            hire: "#22d3ee",
            lean_hire: "#f59e0b",
            lean_no: "#f97316",
            no_hire: "#f43f5e",
          }[e.recommendation] || "#94a3b8",
        dimensions: e.dimensions || {},
        strengths: e.strengths || [],
        areas_for_improvement: e.areas_for_improvement || e.improvements || [],
        improvements: e.improvements || e.areas_for_improvement || [],
        detailed_feedback: e.detailed_feedback || "",
        suggested_practice:
          e.suggested_practice || e.practice_suggestions || [],
        practice_suggestions:
          e.practice_suggestions || e.suggested_practice || [],
        readiness: e.readiness || "",
        tone_analysis: e.tone_analysis || {},
        engagement_summary: e.engagement_summary || {},
      };
    },

    renderRadarChart() {
      const el = document.getElementById("interviewRadarChart");
      if (!el || !this.interviewEvaluation?.dimensions) return;

      const dimOrder = [
        "communication",
        "technical_depth",
        "problem_solving",
        "leadership",
        "tone_and_delivery",
        "engagement",
      ];
      const labels = [];
      const scores = [];
      for (const key of dimOrder) {
        const dim = this.interviewEvaluation.dimensions[key];
        if (dim) {
          labels.push(this.getDimName(key));
          scores.push(dim.score || 0);
        }
      }
      if (labels.length < 3) return;

      // Destroy existing chart
      if (this._radarChart) {
        this._radarChart.destroy();
        this._radarChart = null;
      }

      this._radarChart = new Chart(el, {
        type: "radar",
        data: {
          labels,
          datasets: [
            {
              label: "Score",
              data: scores,
              backgroundColor: "rgba(139, 92, 246, 0.15)",
              borderColor: "rgba(139, 92, 246, 0.7)",
              borderWidth: 2,
              pointBackgroundColor: "rgba(139, 92, 246, 1)",
              pointBorderColor: "#1e293b",
              pointRadius: 4,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            r: {
              min: 0,
              max: 5,
              ticks: {
                stepSize: 1,
                color: "#64748b",
                backdropColor: "transparent",
                font: { size: 9 },
              },
              grid: { color: "rgba(100,116,139,0.15)" },
              angleLines: { color: "rgba(100,116,139,0.15)" },
              pointLabels: {
                color: "#94a3b8",
                font: { size: 10, family: "'JetBrains Mono', monospace" },
              },
            },
          },
          plugins: {
            legend: { display: false },
          },
        },
      });
    },

    // ===================================================================
    // VOICE INTERVIEW
    // ===================================================================

    async initVoiceInterview(sessionId) {
      try {
        // Get mic access
        this.interviewMicStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        this.interviewAudioContext = new (
          window.AudioContext || window.webkitAudioContext
        )({ sampleRate: 16000 });

        // Connect audio WebSocket
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        this.interviewAudioWs = new WebSocket(
          `${wsProto}//${location.host}/ws/interview-audio/${sessionId}`,
        );
        this.interviewAudioWs.onmessage = (evt) =>
          this.handleAudioWsMessage(JSON.parse(evt.data));
        this.interviewAudioWs.onclose = () => {
          this.interviewVoiceStatus = "idle";
        };

        // Set up audio processing
        const source = this.interviewAudioContext.createMediaStreamSource(
          this.interviewMicStream,
        );
        const processor = this.interviewAudioContext.createScriptProcessor(
          4096,
          1,
          1,
        );

        let speechActive = false;
        let silenceStart = 0;
        const SILENCE_THRESHOLD = 0.015;
        const SILENCE_DURATION = 1.5;
        const SPEECH_THRESHOLD = 0.02;
        const SPEECH_MIN = 0.3;
        let speechStart = 0;

        processor.onaudioprocess = (e) => {
          if (
            !this.interviewMicActive ||
            !this.interviewAudioWs ||
            this.interviewAudioWs.readyState !== 1
          )
            return;

          const input = e.inputBuffer.getChannelData(0);
          // Calculate RMS energy
          let sum = 0;
          for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
          const rms = Math.sqrt(sum / input.length);

          const now = Date.now() / 1000;

          if (rms > SPEECH_THRESHOLD) {
            if (!speechActive) {
              if (speechStart === 0) {
                speechStart = now; // Mark start of potential speech
              }
              if (now - speechStart > SPEECH_MIN) {
                speechActive = true;
                this.interviewVoiceStatus = "listening";
              }
            }
            silenceStart = 0; // Reset silence counter while speaking
          } else {
            speechStart = 0; // Reset speech counter during silence
            if (speechActive) {
              if (silenceStart === 0) {
                silenceStart = now;
              }
              if (now - silenceStart > SILENCE_DURATION) {
                // End of speech detected
                speechActive = false;
                silenceStart = 0;
                this.interviewVoiceStatus = "thinking";
                if (this.interviewAudioWs.readyState === 1) {
                  this.interviewAudioWs.send(
                    JSON.stringify({ type: "end_of_speech" }),
                  );
                }
              }
            }
          }

          // Send audio chunk as base64
          if (speechActive && this.interviewAudioWs.readyState === 1) {
            const pcm16 = new Int16Array(input.length);
            for (let i = 0; i < input.length; i++) {
              pcm16[i] = Math.max(
                -32768,
                Math.min(32767, Math.round(input[i] * 32767)),
              );
            }
            const bytes = new Uint8Array(pcm16.buffer);
            let binary = "";
            for (let i = 0; i < bytes.length; i++)
              binary += String.fromCharCode(bytes[i]);
            const b64 = btoa(binary);
            this.interviewAudioWs.send(
              JSON.stringify({ type: "audio_chunk", data: b64 }),
            );
          }
        };

        source.connect(processor);
        processor.connect(this.interviewAudioContext.destination);
        this.interviewMicActive = true;
        this.interviewVoiceStatus = "idle";
      } catch (err) {
        this.notify(`Mic access failed: ${err.message}`, "error");
      }
    },

    handleAudioWsMessage(msg) {
      if (msg.type === "transcript") {
        // Candidate's transcribed speech
        this.interviewMessages.push({ role: "candidate", text: msg.text });
        this.$nextTick(() => this.scrollInterviewChat());
      } else if (msg.type === "audio_response") {
        // Interviewer's response (text + audio)
        this.interviewMessages.push({ role: "interviewer", text: msg.text });
        this.interviewVoiceStatus = "speaking";
        this.$nextTick(() => this.scrollInterviewChat());

        // Play audio response
        if (msg.data) {
          try {
            const audioBytes = Uint8Array.from(atob(msg.data), (c) =>
              c.charCodeAt(0),
            );
            const blob = new Blob([audioBytes], { type: "audio/wav" });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => {
              this.interviewVoiceStatus = "idle";
              URL.revokeObjectURL(url);
            };
            audio.play().catch(() => {
              this.interviewVoiceStatus = "idle";
            });
          } catch {
            this.interviewVoiceStatus = "idle";
          }
        } else {
          this.interviewVoiceStatus = "idle";
        }

        if (msg.should_end) {
          this.interviewEnded = true;
        }
      } else if (msg.type === "error") {
        this.notify(`Voice error: ${msg.error}`, "error");
        this.interviewVoiceStatus = "idle";
      }
    },

    toggleMic() {
      this.interviewMicActive = !this.interviewMicActive;
      if (this.interviewMicActive) {
        this.interviewVoiceStatus = "idle";
      } else {
        this.interviewVoiceStatus = "idle";
      }
    },

    // ===================================================================
    // GEMINI LIVE STREAMING
    // ===================================================================

    async startGeminiLiveMode(sessionId, withVideo = false) {
      try {
        // Get mic access
        this.interviewMicStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        // Use default sample rate AudioContext — we handle resampling explicitly
        this.interviewAudioContext = new (
          window.AudioContext || window.webkitAudioContext
        )();
        // Chrome suspends AudioContext until user gesture — force resume
        if (this.interviewAudioContext.state === "suspended") {
          await this.interviewAudioContext.resume();
        }
        this.geminiAudioNextTime = 0;

        // Connect Gemini Live WebSocket
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        this.geminiLiveWs = new WebSocket(
          `${wsProto}//${location.host}/ws/interview-live/${sessionId}`,
        );

        this.geminiLiveWs.onopen = () => {
          this.geminiLiveActive = true;
          this.interviewSending = false; // unblock UI — voice mode is ready
          this.interviewVoiceStatus = "idle";
          this.addFeedItem(
            "Gemini Live connected — real-time streaming active",
            "#8b5cf6",
          );
        };

        this.geminiLiveWs.onmessage = (evt) => {
          try {
            this.handleGeminiLiveMessage(JSON.parse(evt.data));
          } catch (_) {}
        };

        this.geminiLiveWs.onclose = () => {
          this.geminiLiveActive = false;
          this.interviewVoiceStatus = "idle";
        };

        this.geminiLiveWs.onerror = () => {
          this.geminiLiveActive = false;
          this.interviewVoiceStatus = "idle";
          this.notify("Gemini Live connection error", "error");
        };

        // Set up audio processing (reuse existing ScriptProcessorNode pattern)
        const source = this.interviewAudioContext.createMediaStreamSource(
          this.interviewMicStream,
        );
        const processor = this.interviewAudioContext.createScriptProcessor(
          4096,
          1,
          1,
        );

        let speechActive = false;
        let silenceStart = 0;
        const SILENCE_THRESHOLD = 0.015;
        const SILENCE_DURATION = 1.5;
        const SPEECH_THRESHOLD = 0.02;
        const SPEECH_MIN = 0.3;
        let speechStart = 0;

        processor.onaudioprocess = (e) => {
          if (
            !this.interviewMicActive ||
            !this.geminiLiveWs ||
            this.geminiLiveWs.readyState !== 1
          )
            return;

          const input = e.inputBuffer.getChannelData(0);
          // Calculate RMS energy
          let sum = 0;
          for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
          const rms = Math.sqrt(sum / input.length);

          const now = Date.now() / 1000;

          // VAD: track speech state for UI status only (Gemini handles turn detection automatically)
          if (rms > SPEECH_THRESHOLD) {
            if (!speechActive) {
              if (speechStart === 0) speechStart = now;
              if (now - speechStart > SPEECH_MIN) {
                speechActive = true;
                this.interviewVoiceStatus = "listening";
              }
            }
            silenceStart = 0;
          } else {
            speechStart = 0;
            if (speechActive) {
              if (silenceStart === 0) silenceStart = now;
              if (now - silenceStart > SILENCE_DURATION) {
                speechActive = false;
                silenceStart = 0;
                this.interviewVoiceStatus = "thinking";
              }
            }
          }

          // Downsample to 16kHz PCM16 for Gemini, then send
          if (this.geminiLiveWs.readyState === 1) {
            const srcRate = this.interviewAudioContext.sampleRate;
            const dstRate = 16000;
            const ratio = srcRate / dstRate;
            const outLen = Math.floor(input.length / ratio);
            const pcm16 = new Int16Array(outLen);
            for (let i = 0; i < outLen; i++) {
              const srcIdx = Math.floor(i * ratio);
              pcm16[i] = Math.max(
                -32768,
                Math.min(32767, Math.round(input[srcIdx] * 32767)),
              );
            }
            const bytes = new Uint8Array(pcm16.buffer);
            let binary = "";
            for (let i = 0; i < bytes.length; i++)
              binary += String.fromCharCode(bytes[i]);
            const b64 = btoa(binary);
            this.geminiLiveWs.send(
              JSON.stringify({ type: "audio_chunk", data: b64 }),
            );
          }
        };

        source.connect(processor);
        processor.connect(this.interviewAudioContext.destination);
        this.interviewMicActive = true;
        this.interviewVoiceStatus = "idle";

        // If video mode, start webcam and periodic frame streaming
        if (withVideo) {
          await this.startGeminiVideoCapture(sessionId);
        }
      } catch (err) {
        this.notify(`Gemini Live init failed: ${err.message}`, "error");
        this.geminiLiveActive = false;
      }
    },

    async startGeminiVideoCapture(sessionId) {
      try {
        this.interviewVideoStream = await navigator.mediaDevices.getUserMedia({
          video: true,
          audio: false,
        });
        this.interviewWebcamActive = true;

        // Show preview
        this.$nextTick(() => {
          const video = document.getElementById("webcam-preview");
          if (video) {
            video.srcObject = this.interviewVideoStream;
            video.play().catch(() => {});
          }
        });

        // Start MediaRecorder for session recording
        try {
          const combinedStream = new MediaStream([
            ...this.interviewVideoStream.getVideoTracks(),
            ...(this.interviewMicStream
              ? this.interviewMicStream.getAudioTracks()
              : []),
          ]);
          this.interviewMediaRecorder = new MediaRecorder(combinedStream, {
            mimeType: "video/webm;codecs=vp9,opus",
          });
          this.interviewRecordedChunks = [];
          this.interviewMediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) this.interviewRecordedChunks.push(e.data);
          };
          this.interviewMediaRecorder.start(1000);
        } catch {
          // Recording not supported — continue without it
        }

        // Periodic frame capture: send JPEG frames over Gemini Live WS (every 15s)
        this.interviewFrameTimer = setInterval(() => {
          if (
            this.interviewWebcamActive &&
            this.geminiLiveWs &&
            this.geminiLiveWs.readyState === 1 &&
            !this.interviewEnded
          ) {
            this.captureAndSendGeminiFrame();
          }
        }, 15000);
      } catch (err) {
        this.notify(`Camera access failed: ${err.message}`, "error");
      }
    },

    async captureAndSendGeminiFrame() {
      try {
        const video = document.getElementById("webcam-preview");
        if (!video || video.readyState < 2) return;

        const canvas = document.createElement("canvas");
        canvas.width = 320;
        canvas.height = 240;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, 320, 240);

        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", 0.7),
        );
        const reader = new FileReader();
        reader.onload = () => {
          const b64 = reader.result.split(",")[1];
          if (this.geminiLiveWs && this.geminiLiveWs.readyState === 1) {
            this.geminiLiveWs.send(
              JSON.stringify({ type: "video_frame", data: b64 }),
            );
          }
        };
        reader.readAsDataURL(blob);
      } catch {
        /* best effort */
      }
    },

    handleGeminiLiveMessage(msg) {
      if (msg.type === "transcript") {
        // Filter out Gemini's internal thinking (bold headers like "**Planning**\nI've begun...")
        let text = (msg.text || "").trim();
        if (/^\*\*[^*]+\*\*/.test(text)) return;

        const role = msg.role === "interviewer" ? "interviewer" : "candidate";
        if (text) {
          this.interviewMessages.push({ role, text });
          this.$nextTick(() => this.scrollInterviewChat());
        }

        if (msg.should_end) {
          this.interviewEnded = true;
        }
      } else if (msg.type === "audio_response") {
        // AI audio — queue chunks sequentially so they play as continuous speech
        this.interviewVoiceStatus = "speaking";

        if (msg.text) {
          this.interviewMessages.push({ role: "interviewer", text: msg.text });
          this.$nextTick(() => this.scrollInterviewChat());
        }

        if (msg.data && this.interviewAudioContext) {
          try {
            // Ensure AudioContext is running (Chrome suspends it)
            if (this.interviewAudioContext.state === "suspended") {
              this.interviewAudioContext.resume();
            }

            const raw = atob(msg.data);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

            const pcm16 = new Int16Array(bytes.buffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++)
              float32[i] = pcm16[i] / 32768;

            const audioBuffer = this.interviewAudioContext.createBuffer(
              1,
              float32.length,
              24000,
            );
            audioBuffer.getChannelData(0).set(float32);

            const bufferSource =
              this.interviewAudioContext.createBufferSource();
            bufferSource.buffer = audioBuffer;
            bufferSource.connect(this.interviewAudioContext.destination);

            // Schedule this chunk right after the previous one ends
            const now = this.interviewAudioContext.currentTime;
            // Reset queue if it's stale (gap > 0.5s means new turn)
            if (
              this.geminiAudioNextTime > 0 &&
              now - this.geminiAudioNextTime > 0.5
            ) {
              this.geminiAudioNextTime = 0;
            }
            const startAt = Math.max(now, this.geminiAudioNextTime);
            bufferSource.start(startAt);
            this.geminiAudioNextTime = startAt + audioBuffer.duration;
          } catch {
            this.interviewVoiceStatus = "idle";
          }
        }

        if (msg.should_end) {
          this.interviewEnded = true;
        }
      } else if (msg.type === "turn_complete") {
        // Gemini finished its turn — switch back to listening after audio drains
        const delay = Math.max(
          0,
          (this.geminiAudioNextTime || 0) -
            (this.interviewAudioContext
              ? this.interviewAudioContext.currentTime
              : 0),
        );
        setTimeout(() => {
          this.interviewVoiceStatus = "idle";
        }, delay * 1000);
        this.geminiAudioNextTime = 0;
      } else if (msg.type === "error") {
        this.notify(
          `Gemini Live error: ${msg.error || msg.message || "Unknown error"}`,
          "error",
        );
        this.interviewVoiceStatus = "idle";
      }
    },

    stopGeminiLiveMode() {
      // Close Gemini Live WebSocket
      if (this.geminiLiveWs) {
        if (
          this.geminiLiveWs.readyState === WebSocket.OPEN ||
          this.geminiLiveWs.readyState === WebSocket.CONNECTING
        ) {
          this.geminiLiveWs.close();
        }
        this.geminiLiveWs = null;
      }
      this.geminiLiveActive = false;

      // Stop mic stream
      if (this.interviewMicStream) {
        this.interviewMicStream.getTracks().forEach((t) => t.stop());
        this.interviewMicStream = null;
      }

      // Stop webcam stream
      if (this.interviewVideoStream) {
        this.interviewVideoStream.getTracks().forEach((t) => t.stop());
        this.interviewVideoStream = null;
      }

      // Stop media recorder
      if (
        this.interviewMediaRecorder &&
        this.interviewMediaRecorder.state !== "inactive"
      ) {
        this.interviewMediaRecorder.stop();
      }

      // Stop frame capture timer
      if (this.interviewFrameTimer) {
        clearInterval(this.interviewFrameTimer);
        this.interviewFrameTimer = null;
      }

      // Close audio context
      if (this.interviewAudioContext) {
        this.interviewAudioContext.close().catch(() => {});
        this.interviewAudioContext = null;
      }

      this.interviewMicActive = false;
      this.interviewWebcamActive = false;
      this.interviewVoiceStatus = "idle";
    },

    // ===================================================================
    // OpenAI Realtime — Live Audio Phone Call Interviews
    // ===================================================================

    async startOpenAILiveMode(sessionId) {
      try {
        // Get mic access (audio only — no video for OpenAI)
        this.interviewMicStream = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        // OpenAI Realtime uses 24kHz PCM16
        this.interviewAudioContext = new (
          window.AudioContext || window.webkitAudioContext
        )({ sampleRate: 24000 });

        // Connect to OpenAI Realtime WebSocket endpoint
        const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
        this.openaiLiveWs = new WebSocket(
          `${wsProto}//${location.host}/ws/interview-openai-live/${sessionId}`,
        );

        this.openaiLiveWs.onopen = () => {
          this.openaiLiveActive = true;
          this.interviewVoiceStatus = "idle";
          this.addFeedItem(
            "OpenAI Realtime connected — live audio active",
            "#3b82f6",
          );
        };

        this.openaiLiveWs.onmessage = (evt) => {
          try {
            this.handleOpenAILiveMessage(JSON.parse(evt.data));
          } catch (_) {}
        };

        this.openaiLiveWs.onclose = () => {
          this.openaiLiveActive = false;
          this.interviewVoiceStatus = "idle";
        };

        this.openaiLiveWs.onerror = () => {
          this.openaiLiveActive = false;
          this.interviewVoiceStatus = "idle";
          this.notify("OpenAI Realtime connection error", "error");
        };

        // Set up audio processing — stream all audio (OpenAI has server-side VAD)
        const source = this.interviewAudioContext.createMediaStreamSource(
          this.interviewMicStream,
        );
        const processor = this.interviewAudioContext.createScriptProcessor(
          4096,
          1,
          1,
        );

        processor.onaudioprocess = (e) => {
          if (
            !this.interviewMicActive ||
            !this.openaiLiveWs ||
            this.openaiLiveWs.readyState !== 1
          )
            return;

          const input = e.inputBuffer.getChannelData(0);

          // Convert Float32 to PCM16 and send as base64
          const pcm16 = new Int16Array(input.length);
          for (let i = 0; i < input.length; i++) {
            pcm16[i] = Math.max(
              -32768,
              Math.min(32767, Math.round(input[i] * 32767)),
            );
          }
          const bytes = new Uint8Array(pcm16.buffer);
          let binary = "";
          for (let i = 0; i < bytes.length; i++)
            binary += String.fromCharCode(bytes[i]);
          const b64 = btoa(binary);
          this.openaiLiveWs.send(
            JSON.stringify({ type: "audio_chunk", data: b64 }),
          );
        };

        source.connect(processor);
        processor.connect(this.interviewAudioContext.destination);
        this.interviewMicActive = true;
        this.interviewVoiceStatus = "idle";
      } catch (err) {
        this.notify(`OpenAI Realtime init failed: ${err.message}`, "error");
        this.openaiLiveActive = false;
      }
    },

    handleOpenAILiveMessage(msg) {
      if (msg.type === "transcript") {
        const role = msg.role === "interviewer" ? "interviewer" : "candidate";
        this.interviewMessages.push({ role, text: msg.text });
        this.$nextTick(() => this.scrollInterviewChat());

        if (msg.should_end) {
          this.interviewEnded = true;
        }
      } else if (msg.type === "audio_response") {
        this.interviewVoiceStatus = "speaking";

        if (msg.text) {
          this.interviewMessages.push({ role: "interviewer", text: msg.text });
          this.$nextTick(() => this.scrollInterviewChat());
        }

        if (msg.data && this.interviewAudioContext) {
          try {
            const raw = atob(msg.data);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

            // Decode PCM16 to Float32 for AudioContext playback at 24kHz
            const pcm16 = new Int16Array(bytes.buffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++)
              float32[i] = pcm16[i] / 32768;

            const audioBuffer = this.interviewAudioContext.createBuffer(
              1,
              float32.length,
              24000,
            );
            audioBuffer.getChannelData(0).set(float32);

            const bufferSource =
              this.interviewAudioContext.createBufferSource();
            bufferSource.buffer = audioBuffer;
            bufferSource.connect(this.interviewAudioContext.destination);
            bufferSource.onended = () => {
              this.interviewVoiceStatus = "idle";
            };
            bufferSource.start();
          } catch {
            this.interviewVoiceStatus = "idle";
          }
        } else {
          this.interviewVoiceStatus = "idle";
        }

        if (msg.should_end) {
          this.interviewEnded = true;
        }
      } else if (msg.type === "turn_complete") {
        this.interviewVoiceStatus = "idle";
      } else if (msg.type === "speech_started") {
        // OpenAI detected user started speaking
        this.interviewVoiceStatus = "listening";
      } else if (msg.type === "speech_stopped") {
        // OpenAI detected user stopped speaking
        this.interviewVoiceStatus = "thinking";
      } else if (msg.type === "error") {
        this.notify(
          `OpenAI Realtime error: ${msg.error || msg.message || "Unknown error"}`,
          "error",
        );
        this.interviewVoiceStatus = "idle";
      }
    },

    stopOpenAILiveMode() {
      if (this.openaiLiveWs) {
        if (
          this.openaiLiveWs.readyState === WebSocket.OPEN ||
          this.openaiLiveWs.readyState === WebSocket.CONNECTING
        ) {
          this.openaiLiveWs.close();
        }
        this.openaiLiveWs = null;
      }
      this.openaiLiveActive = false;

      // Stop mic stream
      if (this.interviewMicStream) {
        this.interviewMicStream.getTracks().forEach((t) => t.stop());
        this.interviewMicStream = null;
      }

      // Close audio context
      if (this.interviewAudioContext) {
        this.interviewAudioContext.close().catch(() => {});
        this.interviewAudioContext = null;
      }

      this.interviewMicActive = false;
      this.interviewVoiceStatus = "idle";
    },

    /**
     * Returns true if current interview config will use live streaming
     * (Gemini Live for voice/video, or OpenAI Realtime for voice).
     */
    isLiveMode() {
      const p = this.interviewConfig.provider;
      const m = this.interviewConfig.mode;
      return (
        (p === "gemini" && (m === "voice" || m === "video")) ||
        (p === "openai" && m === "voice")
      );
    },

    /** @deprecated Use isLiveMode() instead */
    isGeminiLiveMode() {
      return (
        this.interviewConfig.provider === "gemini" &&
        (this.interviewConfig.mode === "voice" ||
          this.interviewConfig.mode === "video")
      );
    },

    // ===================================================================
    // VIDEO INTERVIEW
    // ===================================================================

    async initVideoInterview(sessionId) {
      try {
        this.interviewVideoStream = await navigator.mediaDevices.getUserMedia({
          video: true,
          audio: false,
        });
        this.interviewWebcamActive = true;

        // Show preview
        this.$nextTick(() => {
          const video = document.getElementById("webcam-preview");
          if (video) {
            video.srcObject = this.interviewVideoStream;
            video.play().catch(() => {});
          }
        });

        // Start MediaRecorder for session recording
        try {
          const combinedStream = new MediaStream([
            ...this.interviewVideoStream.getVideoTracks(),
            ...(this.interviewMicStream
              ? this.interviewMicStream.getAudioTracks()
              : []),
          ]);
          this.interviewMediaRecorder = new MediaRecorder(combinedStream, {
            mimeType: "video/webm;codecs=vp9,opus",
          });
          this.interviewRecordedChunks = [];
          this.interviewMediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) this.interviewRecordedChunks.push(e.data);
          };
          this.interviewMediaRecorder.start(1000); // 1s chunks
        } catch {
          // Recording not supported — continue without it
        }

        // Periodic frame capture for engagement analysis (every 15s)
        this.interviewFrameTimer = setInterval(() => {
          if (
            this.interviewWebcamActive &&
            this.interviewSession &&
            !this.interviewEnded
          ) {
            this.captureAndAnalyzeFrame(sessionId);
          }
        }, 15000);
      } catch (err) {
        this.notify(`Camera access failed: ${err.message}`, "error");
      }
    },

    async captureAndAnalyzeFrame(sessionId) {
      try {
        const video = document.getElementById("webcam-preview");
        if (!video || video.readyState < 2) return;

        const canvas = document.createElement("canvas");
        canvas.width = 320;
        canvas.height = 240;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, 320, 240);

        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", 0.7),
        );
        const reader = new FileReader();
        reader.onload = async () => {
          const b64 = reader.result.split(",")[1];
          try {
            const res = await fetch("/api/interview/analyze-frame", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: sessionId, frame: b64 }),
            });
            if (res.ok) {
              const data = await res.json();
              if (data.score) {
                this.addFeedItem(
                  `Engagement: ${data.score}/5 — ${data.notes || ""}`,
                  "#a78bfa",
                );
              }
            }
          } catch {
            /* best effort */
          }
        };
        reader.readAsDataURL(blob);
      } catch {
        /* best effort */
      }
    },

    async uploadRecording(sessionId) {
      if (!this.interviewRecordedChunks.length) return;
      try {
        const blob = new Blob(this.interviewRecordedChunks, {
          type: "video/webm",
        });
        const fd = new FormData();
        fd.append("recording", blob, `${sessionId}.webm`);
        await fetch(`/api/interview/${sessionId}/recording`, {
          method: "POST",
          body: fd,
        });
      } catch {
        /* best effort */
      }
    },

    // ===================================================================
    // CHARTS
    // ===================================================================

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
