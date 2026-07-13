// Merz dashboard — front-end logic

let reportLoaded = false;
let volumeChart = null;
let adverseChart = null;
let productChart = null;
let ratingChart = null;
let searchTimer = null;

const appShell = document.getElementById("appShell");
const filterForm = document.getElementById("filterForm");
const startInput = document.getElementById("startDate");
const endInput = document.getElementById("endDate");
const generateBtn = document.getElementById("generateBtn");
const exportBtn = document.getElementById("exportBtn");
const emptyState = document.getElementById("emptyState");
const dashboard = document.getElementById("dashboardContent");
const loader = document.getElementById("loadingOverlay");
const loaderText = document.getElementById("loadingText");
const toastBox = document.getElementById("toastContainer");
const periodBadge = document.getElementById("periodBadge");
const periodLabel = document.getElementById("periodLabel");
const sessionSearch = document.getElementById("sessionSearch");

const today = appShell.dataset.today || "today";


function $(id) {
    return document.getElementById(id);
}

function escapeHtml(text) {
    const el = document.createElement("div");
    el.textContent = text;
    return el.innerHTML;
}

function showToast(message, type) {
    const icons = { success: "check-circle", error: "x-circle", info: "info-circle" };
    const toast = document.createElement("div");
    toast.className = "toast toast-" + (type || "info");
    toast.innerHTML = '<i class="bi bi-' + icons[type || "info"] + '"></i><span>' + escapeHtml(message) + "</span>";
    toastBox.appendChild(toast);
    setTimeout(function () { toast.remove(); }, 5000);
}

function showLoader(on, text) {
    loader.classList.toggle("is-visible", on);
    loaderText.textContent = text || "Syncing data from API…";
    generateBtn.disabled = on;
    exportBtn.disabled = on || !reportLoaded;
}

function showEmptyView() {
    emptyState.classList.add("is-visible");
    emptyState.classList.remove("is-hidden");
    dashboard.classList.remove("is-visible");
    dashboard.classList.add("is-hidden");
    periodBadge.classList.remove("is-visible");
}

function showReportView() {
    emptyState.classList.remove("is-visible");
    emptyState.classList.add("is-hidden");
    dashboard.classList.add("is-visible");
    dashboard.classList.remove("is-hidden");
    periodBadge.classList.add("is-visible");
}

function getSelectedDates() {
    return { start_date: startInput.value, end_date: endInput.value };
}

function isMobile() {
    return window.matchMedia("(max-width: 900px)").matches;
}


// --- Sidebar ---

function setSidebarOpen(open) {
    appShell.classList.toggle("sidebar-collapsed", !open);

    const backdrop = document.getElementById("sidebarBackdrop");
    if (isMobile()) {
        backdrop.classList.toggle("is-visible", open);
    } else {
        backdrop.classList.remove("is-visible");
    }

    localStorage.setItem("merz_sidebar_open", open ? "1" : "0");
}

function setupSidebar() {
    let open = true;
    const saved = localStorage.getItem("merz_sidebar_open");
    if (saved !== null) {
        open = saved === "1";
    } else if (localStorage.getItem("merz_sidebar_collapsed") === "1") {
        open = false; // old key from previous version
    }
    setSidebarOpen(open);

    document.getElementById("sidebarCloseBtn").onclick = function () { setSidebarOpen(false); };
    document.getElementById("sidebarOpenBtn").onclick = function () { setSidebarOpen(true); };
    document.getElementById("emptyOpenSidebar").onclick = function () { setSidebarOpen(true); };
    document.getElementById("sidebarBackdrop").onclick = function () { setSidebarOpen(false); };

    window.addEventListener("resize", function () {
        if (!isMobile()) {
            document.getElementById("sidebarBackdrop").classList.remove("is-visible");
        }
    });
}


// --- Date pickers ---

function setupDatePickers() {
    // Restrict calendar to last 1 month only
    const todayDate = new Date(today);
    const oneMonthAgo = new Date(todayDate);
    oneMonthAgo.setMonth(oneMonthAgo.getMonth() - 1);
    const minAllowed = oneMonthAgo.toISOString().slice(0, 10);

    const opts = {
        dateFormat: "Y-m-d",
        minDate: minAllowed,
        maxDate: today,
        disableMobile: true,
    };

    flatpickr(startInput, {
        ...opts,
        defaultDate: startInput.value,
        onChange: function (_dates, value) {
            if (endInput._flatpickr && value) {
                endInput._flatpickr.set("minDate", value);
            }
        },
    });

    flatpickr(endInput, {
        ...opts,
        defaultDate: endInput.value,
        minDate: startInput.value || minAllowed,
    });
}


// --- API ---

async function apiPost(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error("Bad response from server — check the terminal for errors.");
    }

    if (!response.ok || !data.success) {
        throw new Error(data.error || "Something went wrong");
    }
    return data;
}


// --- Rendering ---

function fillMetrics(m) {
    $("mTotalQuestions").textContent = m.total_questions;
    $("mTotalSessions").textContent = m.total_sessions;
    $("mAvgPerSession").textContent = m.avg_per_session;
    $("mAvgPerDay").textContent = m.avg_per_day;
    $("mClickRate").textContent = m.content_ctr + "%";
    $("mTotalClicks").textContent = m.total_clicks;
    $("mPeakDay").textContent = m.peak_day_count;

    const peakDate = $("mPeakDayDate");
    if (m.peak_day && m.peak_day !== "—") {
        const d = new Date(m.peak_day + "T00:00:00");
        peakDate.textContent = "(" + d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) + ")";
    } else {
        peakDate.textContent = "";
    }

    $("mAdverse").textContent = m.total_adverse;
    $("mAdverseHigh").textContent = m.adverse_high != null ? m.adverse_high : 0;
    $("mAdverseMedium").textContent = m.adverse_medium != null ? m.adverse_medium : 0;
    $("mAdverseLow").textContent = m.adverse_low != null ? m.adverse_low : 0;
    const adverseBreakdown = $("mAdverseBreakdown");
    if (m.total_adverse > 0) {
        adverseBreakdown.classList.remove("is-hidden");
    } else {
        adverseBreakdown.classList.add("is-hidden");
    }

    $("mHcpSessions").textContent = m.hcp_sessions != null ? m.hcp_sessions : "—";
    $("mConsumerSessions").textContent = m.consumer_sessions != null ? m.consumer_sessions : "—";

    $("mZeroClickRate").textContent = m.zero_click_session_rate + "%";
    $("mZeroClickCount").textContent = m.sessions_with_no_click != null
        ? "(" + m.sessions_with_no_click + " session" + (m.sessions_with_no_click === 1 ? "" : "s") + ")"
        : "";
}

function makeChart(canvas, existing, type, labels, values, borderColor, fillColor) {
    if (existing) existing.destroy();

    return new Chart(canvas, {
        type: type,
        data: {
            labels: labels,
            datasets: [{
                data: values,
                borderColor: borderColor,
                backgroundColor: fillColor,
                borderWidth: 2,
                fill: type === "line",
                tension: 0.35,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: "#555555" } },
                y: { beginAtZero: true, ticks: { stepSize: 1, color: "#555555" }, grid: { color: "rgba(0,0,0,0.05)" } },
            },
        },
    });
}

function showChart(canvasId, emptyId, chartRef, hasData) {
    var canvas = $(canvasId);
    var empty = $(emptyId);
    if (hasData) {
        canvas.classList.remove("is-hidden");
        empty.classList.add("is-hidden");
    } else {
        canvas.classList.add("is-hidden");
        empty.classList.remove("is-hidden");
        if (chartRef) { chartRef.destroy(); }
    }
    return hasData;
}

function fillCharts(insights) {
    var labels = insights.daily_trend.map(function (row) {
        var d = new Date(row.date + "T00:00:00");
        return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    });

    var volumeValues = insights.daily_trend.map(function (r) { return r.questions; });
    var hasVolume = volumeValues.some(function (v) { return v > 0; });

    if (showChart("volumeChart", "volumeEmpty", volumeChart, hasVolume)) {
        volumeChart = makeChart(
            $("volumeChart"), volumeChart, "line", labels,
            volumeValues, "#000000", "rgba(0, 0, 0, 0.07)"
        );
    } else {
        volumeChart = null;
    }

    var adverseValues = insights.adverse_trend.map(function (r) { return r.adverse_events; });
    var hasAdverse = adverseValues.some(function (v) { return v > 0; });

    if (showChart("adverseChart", "adverseEmpty", adverseChart, hasAdverse)) {
        adverseChart = makeChart(
            $("adverseChart"), adverseChart, "bar", labels,
            adverseValues, "#ef4444", "rgba(239, 68, 68, 0.65)"
        );
    } else {
        adverseChart = null;
    }
}

function fillSurveyPanel(survey) {
    const summary = $("surveySummary");
    const empty = $("surveyEmpty");

    if (!survey || survey.total === 0) {
        summary.classList.add("is-hidden");
        empty.classList.remove("is-hidden");
        if (ratingChart) { ratingChart.destroy(); ratingChart = null; }
        return;
    }

    empty.classList.add("is-hidden");
    summary.classList.remove("is-hidden");

    $("sSurveyTotal").textContent = survey.total;
    $("sSolvedPct").textContent = survey.solved_pct + "%";
    $("sAvgRating").textContent = survey.avg_rating + " / 5";

    if (ratingChart) ratingChart.destroy();
    ratingChart = new Chart($("ratingChart"), {
        type: "bar",
        data: {
            labels: ["1 ★", "2 ★★", "3 ★★★", "4 ★★★★", "5 ★★★★★"],
            datasets: [{
                label: "Responses",
                data: survey.rating_dist.map(function (r) { return r.count; }),
                backgroundColor: ["#ef4444", "#f97316", "#f59e0b", "#84cc16", "#22c55e"],
                borderRadius: 5,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: "#555555" }, grid: { color: "rgba(0,0,0,0.05)" } },
                y: { beginAtZero: true, ticks: { color: "#555555", precision: 0 }, grid: { color: "rgba(0,0,0,0.05)" } },
            },
        },
    });
}


function fillProductChart(productInteractions) {
    const hasData = productInteractions && productInteractions.length > 0;

    if (!showChart("productChart", "productEmpty", productChart, hasData)) {
        productChart = null;
        return;
    }

    const labels = productInteractions.map(function (p) { return p.product; });
    const values = productInteractions.map(function (p) { return p.sessions; });
    const colors = ["#333333", "#555555", "#777777", "#999999", "#bbbbbb", "#dddddd"];

    if (productChart) productChart.destroy();

    productChart = new Chart($("productChart"), {
        type: "bar",
        data: {
            labels: labels,
            datasets: [{
                label: "Sessions",
                data: values,
                backgroundColor: colors.slice(0, labels.length),
                borderRadius: 6,
                borderSkipped: false,
                maxBarThickness: 48,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: "y",
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (ctx) { return " " + ctx.raw + " session" + (ctx.raw === 1 ? "" : "s"); }
                    }
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    ticks: { color: "#555555", precision: 0 },
                    grid: { color: "rgba(0,0,0,0.05)" },
                },
                y: {
                    ticks: { color: "#333333" },
                    grid: { display: false },
                },
            },
        },
    });
}


function fillTable(tableId, emptyId, rows, buildRow) {
    const table = $(tableId);
    const tbody = table.querySelector("tbody");
    const emptyMsg = $(emptyId);

    tbody.innerHTML = "";

    if (!rows.length) {
        table.classList.add("is-hidden");
        emptyMsg.classList.remove("is-hidden");
        return;
    }

    table.classList.remove("is-hidden");
    emptyMsg.classList.add("is-hidden");

    rows.forEach(function (row, i) {
        const tr = document.createElement("tr");
        tr.innerHTML = buildRow(row, i);
        tbody.appendChild(tr);
    });
}

function buildSurveyHtml(survey) {
    if (!survey) return "";
    const stars = survey.rating
        ? "★".repeat(survey.rating) + "☆".repeat(5 - survey.rating)
        : "—";
    const solvedCls = survey.solved === "Yes" ? "survey-solved-yes" : "survey-solved-no";
    return '<div class="session-survey">' +
        '<div class="session-survey-header"><i class="bi bi-clipboard-check"></i> Survey Response</div>' +
        '<div class="session-survey-row"><span>Problem solved?</span>' +
        '<span class="' + solvedCls + '">' + escapeHtml(survey.solved || "—") + "</span></div>" +
        '<div class="session-survey-row"><span>Quality rating</span>' +
        '<span class="survey-stars">' + stars + "</span></div>" +
        (survey.comment
            ? '<div class="session-survey-comment"><i class="bi bi-chat-left-quote"></i> ' + escapeHtml(survey.comment) + "</div>"
            : "") +
        "</div>";
}


function fillSessions(sessions, surveyBySession) {
    const list = $("sessionsList");
    const emptyMsg = $("sessionsEmpty");
    const countLine = $("sessionCount");

    list.innerHTML = "";

    if (!sessions.length) {
        emptyMsg.classList.remove("is-hidden");
        countLine.textContent = "";
        return;
    }

    emptyMsg.classList.add("is-hidden");

    let questionTotal = 0;
    sessions.forEach(function (s) { questionTotal += s.questions.length; });
    countLine.textContent = sessions.length + " session(s), " + questionTotal + " question(s)";

    sessions.forEach(function (session) {
        const item = document.createElement("div");
        item.className = "session-item";

        const qCount = session.questions.length;
        let questionsHtml = "";

        session.questions.forEach(function (q) {
            let clicksHtml = '<span class="no-clicks">No recommendations shown</span>';
            if (q.recommendations && q.recommendations.length) {
                clicksHtml = '<ul class="recs-list">';
                q.recommendations.forEach(function (rec) {
                    const clicked = rec.clicked;
                    const icon = clicked
                        ? '<i class="bi bi-check-circle-fill rec-clicked"></i>'
                        : '<i class="bi bi-circle rec-unclicked"></i>';
                    const cls = clicked ? "rec-item rec-item--clicked" : "rec-item rec-item--skipped";
                    const extBadge = rec.external ? ' <span class="rec-ext-badge">ext</span>' : "";
                    clicksHtml += '<li class="' + cls + '">' + icon + escapeHtml(rec.title) + extBadge + "</li>";
                });
                clicksHtml += "</ul>";
            }

            questionsHtml +=
                '<div class="question-block">' +
                '<div class="question-text"><span class="question-date">' + escapeHtml(q.date) + "</span> — " + escapeHtml(q.question) + "</div>" +
                clicksHtml +
                "</div>";
        });

        const survey = surveyBySession && surveyBySession[session.session_id];
        const surveyBadge = survey
            ? '<span class="survey-badge"><i class="bi bi-clipboard-check"></i> Survey</span>'
            : "";
        const surveyHtml = buildSurveyHtml(survey);

        item.innerHTML =
            '<button type="button" class="session-toggle">' +
            '<div class="session-meta">' +
            "<span><strong>Session:</strong> " + escapeHtml(session.session_id) + "</span>" +
            "<span><strong>Date:</strong> " + escapeHtml(session.date || "—") + "</span>" +
            "<span><strong>Source:</strong> " + escapeHtml(session.source || "—") + "</span>" +
            "</div>" +
            '<div class="session-toggle-right">' +
            surveyBadge +
            '<span class="session-badge">' + qCount + " question" + (qCount === 1 ? "" : "s") + "</span>" +
            '<i class="bi bi-chevron-down session-chevron"></i>' +
            "</div>" +
            "</button>" +
            '<div class="session-body">' + questionsHtml + surveyHtml + "</div>";

        const toggle = item.querySelector(".session-toggle");
        const body = item.querySelector(".session-body");
        toggle.addEventListener("click", function () {
            const open = body.classList.toggle("open");
            toggle.classList.toggle("open", open);
        });

        item.querySelectorAll(".question-block").forEach(function (block) {
            block.addEventListener("click", function () {
                block.classList.toggle("question-selected");
            });
        });

        list.appendChild(item);
    });
}

function updateDashboard(data) {
    reportLoaded = true;
    showReportView();

    exportBtn.disabled = false;
    sessionSearch.disabled = false;
    periodLabel.textContent = data.period_label;

    fillMetrics(data.insights.metrics);
    fillCharts(data.insights);
    fillProductChart(data.insights.product_interactions);
    fillSurveyPanel(data.insights.survey);

    fillTable("topQuestionsTable", "topQuestionsEmpty", data.insights.top_questions, function (q, i) {
        return "<td>" + (i + 1) + "</td><td>" + escapeHtml(q.question) + "</td><td>" + q.count + "</td>";
    });

    fillTable("topContentTable", "topContentEmpty", data.insights.top_clicked_content, function (c, i) {
        return "<td>" + (i + 1) + "</td><td>" + escapeHtml(c.content) + "</td><td>" + c.clicks + "</td>";
    });

    fillTable("contentVolumeTable", "contentVolumeEmpty", data.insights.content_volume, function (c, i) {
        const ctrClass = c.ctr >= 30 ? "ctr-high" : c.ctr >= 10 ? "ctr-mid" : "ctr-low";
        return "<td>" + (i + 1) + "</td>" +
               "<td>" + escapeHtml(c.title) + "</td>" +
               "<td>" + c.impressions + "</td>" +
               "<td>" + c.clicks + "</td>" +
               "<td><span class='ctr-badge " + ctrClass + "'>" + c.ctr + "%</span></td>";
    });

    fillSessions(data.sessions, data.survey_by_session);

    fillTable("adverseTable", "adverseEmpty", data.adverse_events, function (e) {
        return "<td>" + escapeHtml(e.date) + "</td><td>" + escapeHtml(e.question) + "</td><td>" + escapeHtml(e.reason) + "</td><td>" + escapeHtml(e.source || "—") + "</td>";
    });

    const caption = $("adverseCaption");
    caption.textContent = data.adverse_events.length
        ? "Found " + data.adverse_events.length + " flagged transcript(s)"
        : "";

    if (isMobile()) setSidebarOpen(false);
}


// --- Events ---

async function loadReport(start_date, end_date, silent) {
    showLoader(true, "Syncing data from API…");
    try {
        const data = await apiPost("/api/sync", {
            start_date,
            end_date,
            search: sessionSearch.value.trim(),
        });
        updateDashboard(data);
        if (!silent) showToast(data.message, "success");
    } catch (err) {
        throw err;
    } finally {
        showLoader(false);
    }
}

filterForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    const dates = getSelectedDates();
    if (dates.start_date > dates.end_date) {
        showToast("End date must be on or after start date.", "error");
        return;
    }

    try {
        await loadReport(dates.start_date, dates.end_date);
        showToast("Report loaded.", "success");
    } catch (err) {
        showToast(err.message, "error");
    }
});

exportBtn.addEventListener("click", async function () {
    const dates = getSelectedDates();
    showLoader(true, "Generating Excel report…");
    try {
        const data = await apiPost("/api/export", dates);
        showToast(data.message, "success");
        window.location.href = data.download_url;
    } catch (err) {
        showToast(err.message, "error");
    } finally {
        showLoader(false);
    }
});

sessionSearch.addEventListener("input", function () {
    if (!reportLoaded) return;

    clearTimeout(searchTimer);
    searchTimer = setTimeout(async function () {
        const dates = getSelectedDates();
        try {
            const data = await apiPost("/api/refresh", {
                start_date: dates.start_date,
                end_date: dates.end_date,
                search: sessionSearch.value.trim(),
            });
            fillSessions(data.sessions, data.survey_by_session);
        } catch (err) {
            showToast(err.message, "error");
        }
    }, 300);
});


// --- Start ---

setupSidebar();
setupDatePickers();

// Auto-load last 2 days on first open (silent — no success toast)
(async function autoLoad() {
    try {
        const dates = getSelectedDates();
        await loadReport(dates.start_date, dates.end_date, true);
    } catch (err) {
        showToast("Could not load data: " + err.message, "error");
        showEmptyView();
    }
})();
