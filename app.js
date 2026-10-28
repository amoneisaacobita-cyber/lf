const verses = [
  { text: "The Lord is my strength and my shield; my heart trusts in him, and he helps me.", reference: "PSALM 28:7" },
  { text: "Be still, and know that I am God.", reference: "PSALM 46:10" },
  { text: "I can do all this through him who gives me strength.", reference: "PHILIPPIANS 4:13" },
  { text: "Let all that you do be done in love.", reference: "1 CORINTHIANS 16:14" },
  { text: "Those who hope in the Lord will renew their strength.", reference: "ISAIAH 40:31" },
  { text: "The Lord is good to all; he has compassion on all he has made.", reference: "PSALM 145:9" },
  { text: "Do everything in love.", reference: "1 CORINTHIANS 16:14" },
  { text: "Cast all your anxiety on him because he cares for you.", reference: "1 PETER 5:7" },
  { text: "Rejoice in hope, be patient in tribulation, be constant in prayer.", reference: "ROMANS 12:12" }
];

const sermons = [
  { title: "Finding stillness in the noise", speaker: "Pastor Michael Reed", date: "October 25, 2026", duration: "32 min", topic: "Faith", art: "art-sand", symbol: "✳" },
  { title: "A hope that holds us", speaker: "Pastor Rachel James", date: "October 18, 2026", duration: "28 min", topic: "Hope", art: "art-mist", symbol: "⌁" },
  { title: "Making room at the table", speaker: "Pastor David Kim", date: "October 11, 2026", duration: "35 min", topic: "Community", art: "art-dusk", symbol: "⌂" },
  { title: "The courage to begin again", speaker: "Pastor Michael Reed", date: "October 4, 2026", duration: "30 min", topic: "Faith", art: "art-olive", symbol: "↗" },
  { title: "Held through every season", speaker: "Pastor Rachel James", date: "September 27, 2026", duration: "26 min", topic: "Hope", art: "art-coral", symbol: "✿" },
  { title: "Love your neighbor well", speaker: "Pastor David Kim", date: "September 20, 2026", duration: "34 min", topic: "Community", art: "art-blue", symbol: "♡" }
];

const shorts = [
  { title: "A breath prayer for busy days", subtitle: "PASTOR RACHEL · 0:42", art: "art-mist", symbol: "☼" },
  { title: "You belong at the table", subtitle: "PASTOR DAVID · 0:58", art: "art-coral", symbol: "⌂" },
  { title: "A small reminder for today", subtitle: "PASTOR MICHAEL · 0:35", art: "art-olive", symbol: "✳" },
  { title: "Make space for hope", subtitle: "PASTOR RACHEL · 0:51", art: "art-dusk", symbol: "✿" },
  { title: "Grace for the in-between", subtitle: "PASTOR MICHAEL · 0:47", art: "art-sand", symbol: "⌁" },
  { title: "Let kindness lead", subtitle: "PASTOR DAVID · 0:39", art: "art-blue", symbol: "♡" }
];

const pageNames = { home: "Home", sermons: "Sermons", shorts: "Short videos", prayer: "Prayer requests", announcements: "Announcements", verses: "Daily verses", bible: "King James Bible", community: "Community", about: "About us", admin: "Admin dashboard", studio: "Streaming studio" };
let pastors = [];
let prayerRequests = [];
let churchVerses = [];
let churchProfile = null;
let announcements = [];
let bibleBooks = null;
let uploadedSermons = [];
let uploadedShorts = [];
let currentUser = null;
let setupRequired = false;
let studioUnlocked = false;
let activeStream = null;
let activeRecorder = null;
let pendingSermonCover = null;
let uploadSequence = 0;
let uploadQueue = Promise.resolve();
let uploadError = null;
let recordingContext = null;
let recordingSource = null;
let recordingProcessor = null;
let recordingSampleRate = 16000;
let recordingResamplePhase = 0;
let listenerTimer = null;
let listenerSequence = 0;
let mediaSource = null;
let sourceBuffer = null;
let listenerActive = false;
let listenerAutoplayBlocked = false;
let listenerStartedPlayback = false;
let queuedAudioSegments = [];
let pendingListenerStreamId = null;
const LIVE_AUDIO_POLL_INTERVAL_MS = 150;
const LIVE_AUDIO_STARTUP_BUFFER_SECONDS = 1.25;
const LIVE_AUDIO_DOWNLOAD_BATCH_SIZE = 8;
let pendingPage = null;
let activeFilter = "All";
let reverseSermons = false;
let toastTimeout;
let liveRecording = false;
let recordingChunks = [];
let activeCommentTitle = "";
const comments = new Map();

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof Blob) && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin", cache: "no-store" });
  const contentType = response.headers.get("content-type") || "";
  const result = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(result?.error || `Request failed (${response.status}).`);
  return result;
}

function setStaffAccess(user) {
  currentUser = user;
  document.querySelector(".admin-nav").hidden = user?.role !== "admin";
  document.querySelector("#pastorsManagement").hidden = user?.role !== "admin";
  document.querySelector("#announcementComposePanel").hidden = !user;
  document.querySelector("#churchLogoForm").hidden = user?.role !== "admin";
  document.querySelector("#removeChurchLogo").hidden = user?.role !== "admin" || !churchProfile?.logo_url;
  const accountButton = document.querySelector("#accountButton");
  accountButton.textContent = user ? user.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toUpperCase() : "→";
  accountButton.setAttribute("aria-label", user ? `Sign out ${user.name}` : "Sign in to staff account");
  document.querySelector(".profile-details strong").textContent = user?.name || "Church member";
  document.querySelector(".profile-details span").textContent = user?.role || "Member";
  document.querySelector(".profile-avatar").textContent = user ? user.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toUpperCase() : "LF";
}

function configureAuthDialog(mode) {
  const isSetup = mode === "setup";
  document.querySelector("#authHeading").textContent = isSetup ? "Create your admin account" : "Staff sign in";
  document.querySelector("#authEyebrow").textContent = isSetup ? "FIRST-TIME SETUP" : "STAFF ACCESS";
  document.querySelector("#authDescription").textContent = isSetup
    ? "Create the administrator account for Life Fountain Church. Enter the one-time setup code printed in the server window and a password with at least 8 characters."
    : "Sign in with the account created by your admin.";
  document.querySelector("#authNameLabel").hidden = !isSetup;
  document.querySelector("#authName").hidden = !isSetup;
  document.querySelector("#authName").required = isSetup;
  document.querySelector("#authCodeLabel").hidden = !isSetup;
  document.querySelector("#authSetupCode").hidden = !isSetup;
  document.querySelector("#authSetupCode").required = isSetup;
  document.querySelector("#authPassword").autocomplete = isSetup ? "new-password" : "current-password";
  document.querySelector("#authSubmit").textContent = isSetup ? "Create admin account" : "Sign in";
  document.querySelector("#authCancel").hidden = isSetup;
  document.querySelector("#authMessage").textContent = "";
  document.querySelector("#authForm").dataset.mode = mode;
}

function openStaffAuth() {
  if (currentUser) {
    if (liveRecording) {
      showToast("Stop and save the live stream before signing out.");
      return;
    }
    apiRequest("/api/logout", { method: "POST", body: "{}" }).then(async () => {
      setStaffAccess(null);
      studioUnlocked = false;
      pastors = [];
      prayerRequests = [];
      renderPastors();
      renderPrayerInbox();
      await refreshRecordings();
      showPage("home");
      showToast("You have signed out.");
    }).catch((error) => showToast(`Could not sign out: ${error.message}`));
    return;
  }
  configureAuthDialog(setupRequired ? "setup" : "login");
  document.querySelector("#authDialog").showModal();
  document.querySelector("#authEmail").focus();
}

async function refreshStaffData() {
  if (!currentUser) return;
  if (currentUser.role === "admin") {
    pastors = await apiRequest("/api/pastors");
    renderPastors();
  }
  prayerRequests = await apiRequest("/api/prayers");
  renderPrayerInbox();
}

async function loadPublicPastors() {
  pastors = await apiRequest("/api/pastors/public");
  renderPastors();
}

async function loadChurchVerses() {
  churchVerses = await apiRequest("/api/verses");
  churchVerses.forEach((verse) => {
    if (!verses.some((existing) => existing.id === verse.id)) verses.push(verse);
  });
  refreshVerses();
  renderChurchVerseManagement();
}

async function loadChurchProfile() {
  churchProfile = await apiRequest("/api/church-profile");
  renderChurchProfile();
  if (currentUser?.role === "admin") {
    document.querySelector("#churchNameInput").value = churchProfile.name || "";
    document.querySelector("#churchLeaderInput").value = churchProfile.leader || "";
    document.querySelector("#churchPhoneInput").value = churchProfile.phone || "";
    document.querySelector("#churchEmailInput").value = churchProfile.email || "";
    document.querySelector("#churchAddressInput").value = churchProfile.address || "";
    document.querySelector("#churchAboutInput").value = churchProfile.about || "";
    document.querySelector("#churchLatitudeInput").value = churchProfile.latitude ?? "";
    document.querySelector("#churchLongitudeInput").value = churchProfile.longitude ?? "";
  }
}

function renderChurchProfile() {
  if (!churchProfile) return;
  for (const [imageSelector, fallbackSelector] of [
    ["#brandLogo", "#brandLogoFallback"],
    ["#churchLogo", "#churchLogoFallback"],
    ["#startupLogo", "#startupLogoFallback"],
    ["#logoPreview", "#logoPreviewFallback"]
  ]) {
    const image = document.querySelector(imageSelector);
    const fallback = document.querySelector(fallbackSelector);
    image.hidden = !churchProfile.logo_url;
    fallback.hidden = Boolean(churchProfile.logo_url);
    if (churchProfile.logo_url) image.src = churchProfile.logo_url;
    else image.removeAttribute("src");
  }
  document.querySelector("#removeChurchLogo").hidden =
    currentUser?.role !== "admin" || !churchProfile.logo_url;
  document.querySelector("#aboutChurchName").textContent = churchProfile.name || "About us";
  document.querySelector(".church-details strong").textContent = churchProfile.name || "Life Fountain Church";
  document.querySelector("#aboutChurchMessage").textContent =
    churchProfile.about || "We would love to connect with you.";
  document.querySelector("#aboutLeader").textContent = churchProfile.leader || "Life Fountain Church";
  document.querySelector("#aboutLocation").textContent = churchProfile.address || "Location details coming soon.";
  const phone = document.querySelector("#aboutPhone");
  phone.hidden = !churchProfile.phone;
  phone.textContent = churchProfile.phone || "";
  phone.href = churchProfile.phone ? `tel:${churchProfile.phone.replace(/[^\d+]/g, "")}` : "";
  const email = document.querySelector("#aboutEmail");
  email.hidden = !churchProfile.email;
  email.textContent = churchProfile.email || "";
  email.href = churchProfile.email ? `mailto:${churchProfile.email}` : "";
  const map = document.querySelector("#churchMap");
  const directions = document.querySelector("#directionsLink");
  const hasCoordinates = Number.isFinite(churchProfile.latitude) && Number.isFinite(churchProfile.longitude);
  map.hidden = !hasCoordinates;
  directions.hidden = !hasCoordinates;
  document.querySelector("#mapEmptyMessage").hidden = hasCoordinates;
  if (!hasCoordinates) {
    map.removeAttribute("src");
    directions.removeAttribute("href");
    return;
  }
  const latitude = churchProfile.latitude;
  const longitude = churchProfile.longitude;
  map.title = `Map showing ${churchProfile.name || "the church"} location`;
  const delta = 0.012;
  const bounds = [
    Math.max(-180, longitude - delta),
    Math.max(-90, latitude - delta),
    Math.min(180, longitude + delta),
    Math.min(90, latitude + delta)
  ].join(",");
  map.src = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bounds)}&layer=mapnik&marker=${encodeURIComponent(`${latitude},${longitude}`)}`;
  directions.href = `https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=;${encodeURIComponent(`${latitude},${longitude}`)}`;
}

async function loadAnnouncements() {
  announcements = await apiRequest("/api/announcements");
  renderAnnouncements();
}

function renderAnnouncements() {
  const list = document.querySelector("#announcementList");
  if (!list) return;
  list.innerHTML = announcements.length
    ? announcements.map((item) => `<article class="announcement-card">
        <div class="announcement-heading"><div><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.author)} · ${escapeHtml(new Date(item.created_at * 1000).toLocaleString())}</p></div><span class="announcement-audience">${item.audience === "pastors" ? "Pastors only" : "Everyone"}</span></div>
        <p class="announcement-message">${escapeHtml(item.message).replace(/\n/g, "<br>")}</p>
      </article>`).join("")
    : '<div class="empty-announcements">There are no announcements yet.</div>';
}

function renderChurchVerseManagement() {
  const list = document.querySelector("#adminVerseList");
  if (!list) return;
  list.innerHTML = churchVerses.length
    ? churchVerses.map((verse) => `<article class="admin-verse-row"><div><strong>${escapeHtml(verse.reference)}</strong><p>${escapeHtml(verse.text)}</p></div><button class="delete-church-verse" data-verse-id="${escapeHtml(verse.id)}" data-verse-reference="${escapeHtml(verse.reference)}" aria-label="Delete ${escapeHtml(verse.reference)}">Delete</button></article>`).join("")
    : '<p class="empty-inbox">No custom verses added yet.</p>';
}

function renderBibleChapter() {
  if (!bibleBooks) return;
  const bookIndex = Number(document.querySelector("#bibleBook").value || 0);
  const chapterIndex = Number(document.querySelector("#bibleChapter").value || 0);
  const book = bibleBooks[bookIndex];
  const chapter = book?.chapters[chapterIndex];
  if (!book || !chapter) return;
  const query = document.querySelector("#bibleSearch").value.trim().toLowerCase();
  const list = document.querySelector("#bibleVerses");
  const title = document.querySelector("#bibleReadingTitle");
  const note = document.querySelector("#bibleResultsNote");
  if (query.length >= 2) {
    const matches = [];
    for (const bibleBook of bibleBooks) {
      for (const bibleChapter of bibleBook.chapters) {
        for (const verse of bibleChapter.verses) {
          const reference = `${bibleBook.book} ${bibleChapter.chapter}:${verse.verse}`;
          if (`${reference} ${verse.text}`.toLowerCase().includes(query)) {
            matches.push({ reference, text: verse.text });
            if (matches.length === 100) break;
          }
        }
        if (matches.length === 100) break;
      }
      if (matches.length === 100) break;
    }
    title.textContent = `Search results${matches.length === 100 ? " (first 100)" : ""}`;
    note.textContent = matches.length
      ? `${matches.length === 100 ? "Showing the first 100 matches." : `${matches.length} match${matches.length === 1 ? "" : "es"} found.`}`
      : "No verses match your search.";
    list.innerHTML = matches.map((verse) => `<p class="bible-verse"><span class="bible-reference">${escapeHtml(verse.reference)}</span>${escapeHtml(verse.text)}</p>`).join("");
    return;
  }
  title.textContent = `${book.book} ${chapter.chapter}`;
  note.textContent = query ? "Enter at least two characters to search." : "";
  list.innerHTML = chapter.verses.map((verse) => `<p class="bible-verse"><sup>${escapeHtml(verse.verse)}</sup>${escapeHtml(verse.text)}</p>`).join("");
}

function updateBibleChapterOptions() {
  if (!bibleBooks) return;
  const book = bibleBooks[Number(document.querySelector("#bibleBook").value || 0)];
  document.querySelector("#bibleChapter").innerHTML = book.chapters
    .map((chapter, index) => `<option value="${index}">${escapeHtml(chapter.chapter)}</option>`)
    .join("");
  renderBibleChapter();
}

async function loadBible() {
  if (bibleBooks) return;
  const note = document.querySelector("#bibleResultsNote");
  note.textContent = "Loading the King James Bible…";
  const response = await fetch("/bible-kjv.json", { credentials: "same-origin", cache: "force-cache" });
  if (!response.ok) throw new Error(`Could not load the Bible (${response.status}).`);
  const books = await response.json();
  if (!Array.isArray(books) || books.length !== 66 || books.some((book) =>
    typeof book.book !== "string" || !Array.isArray(book.chapters) ||
    book.chapters.some((chapter) => !Array.isArray(chapter.verses)))) {
    throw new Error("The Bible data file is incomplete or invalid.");
  }
  bibleBooks = books;
  document.querySelector("#bibleBook").innerHTML = books
    .map((book, index) => `<option value="${index}">${escapeHtml(book.book)}</option>`)
    .join("");
  updateBibleChapterOptions();
}

async function refreshMedia() {
  const [items, recordings] = await Promise.all([
    apiRequest("/api/media"),
    apiRequest("/api/recordings")
  ]);
  uploadedSermons = [
    ...recordings.map((item) => ({
      ...item,
      audio_url: `/api/recordings/${item.id}`,
      speaker: "Life Fountain Church",
      date: new Date(item.started_at * 1000).toLocaleDateString(),
      duration: "Live recording",
      topic: "Sermon",
      art: "art-sand",
      symbol: "♫",
      cover_url: item.cover_url,
      delete_url: `/api/recordings/${encodeURIComponent(item.id)}`,
      edit_url: `/api/recordings/${encodeURIComponent(item.id)}/title`,
      view_url: `/api/recordings/${encodeURIComponent(item.id)}/view`
    })),
    ...items.filter((item) => item.kind === "sermon").map((item) => ({
      ...item,
      speaker: item.speaker,
      date: new Date(item.created_at * 1000).toLocaleDateString(),
      duration: "Audio sermon",
      topic: "Sermon",
      art: "art-olive",
      symbol: "♫",
      delete_url: `/api/media/${encodeURIComponent(item.id)}`,
      edit_url: `/api/media/${encodeURIComponent(item.id)}/title`
    }))
  ];
  uploadedShorts = items.filter((item) => item.kind === "short").map((item) => ({
    ...item,
    subtitle: `${item.speaker} · VIDEO`,
    art: "art-mist",
    symbol: "▷"
  }));
  renderSermons();
  renderShorts();
  if (window.location.hash) openSharedContent();
}

async function initializeServerApp() {
  try {
    const status = await apiRequest("/api/status");
    setupRequired = status.setup_required;
    studioUnlocked = status.studio_unlocked;
    setStaffAccess(status.user);
    document.querySelectorAll("[data-server-badge]").forEach((badge) => {
      badge.textContent = "SERVER ONLINE";
      badge.classList.remove("server-offline");
    });
    await loadPublicPastors();
    const optionalLoads = await Promise.allSettled([
      refreshMedia(),
      refreshLiveStream(),
      refreshRecordings(),
      loadChurchProfile(),
      loadChurchVerses(),
      loadAnnouncements()
    ]);
    optionalLoads.filter((result) => result.status === "rejected").forEach((result) => {
      console.error("Could not load a church feature:", result.reason);
      showToast(`Could not load church information: ${result.reason.message}`);
    });
    if (currentUser) await refreshStaffData();
    if (setupRequired) {
      configureAuthDialog("setup");
      document.querySelector("#authDialog").showModal();
    } else if (window.location.hash === "#studio") {
      showPage("studio");
    } else if (window.location.hash === "#admin") {
      showPage("admin");
    }
    window.setInterval(async () => {
      try {
        await refreshMedia();
        await refreshLiveStream();
        if (currentUser) await refreshStaffData();
      } catch (error) {
        console.error("Background refresh failed:", error);
      }
    }, 10000);
  } catch (error) {
    console.error("Could not connect to Life Fountain Church server:", error);
    document.querySelectorAll("[data-server-badge]").forEach((badge) => {
      badge.textContent = "SERVER OFFLINE";
      badge.classList.add("server-offline");
    });
    showToast(`Server connection failed: ${error.message}`);
    document.querySelector("#streamDescription").textContent = "Server unavailable. Start the website server and reload.";
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function sermonCard(sermon, index) {
  return `<article class="sermon-card" data-content-title="${escapeHtml(sermon.title)}">
    <div class="sermon-art ${sermon.art}${sermon.cover_url ? " has-cover" : ""}">${sermon.cover_url ? `<img class="sermon-cover" src="${escapeHtml(sermon.cover_url)}" alt="${escapeHtml(sermon.title)} wallpaper">` : ""}<span class="art-tag">${escapeHtml(sermon.topic.toUpperCase())}</span><div class="art-illustration">${escapeHtml(sermon.symbol)}</div><button class="card-play" data-play="${index}" data-audio-url="${escapeHtml(sermon.audio_url || "")}" aria-label="Play ${escapeHtml(sermon.title)}">▶</button></div>
    <div class="sermon-info"><h3>${escapeHtml(sermon.title)}</h3><div class="sermon-meta">${escapeHtml(sermon.date)} <span>·</span> ${escapeHtml(sermon.duration)}${sermon.view_url ? ` <span>·</span> <span class="sermon-view-count" data-view-url="${escapeHtml(sermon.view_url)}">${Number(sermon.view_count || 0)} views</span>` : ""}</div><div class="sermon-playback-controls"${sermon.audio_url ? "" : " hidden"}><button class="button button-primary sermon-play-button" data-play="${index}" data-audio-url="${escapeHtml(sermon.audio_url || "")}" type="button">▶ Play</button><button class="button button-outline sermon-stop-button" data-sermon-stop data-audio-url="${escapeHtml(sermon.audio_url || "")}" type="button" disabled>■ Stop</button></div>${currentUser?.role === "admin" && sermon.edit_url ? `<button class="edit-sermon-button" data-edit-url="${escapeHtml(sermon.edit_url)}" data-edit-title="${escapeHtml(sermon.title)}">Edit sermon name</button>` : ""}${currentUser?.role === "admin" && sermon.delete_url ? `<button class="delete-sermon-button" data-delete-url="${escapeHtml(sermon.delete_url)}" data-delete-title="${escapeHtml(sermon.title)}">Delete sermon</button>` : ""}<div class="sermon-bottom"><span class="sermon-speaker">${escapeHtml(sermon.speaker)}</span><div class="sermon-actions"><button class="mini-action like-button" aria-label="Like sermon" aria-pressed="false">♡</button><button class="mini-action comment-button" data-title="${escapeHtml(sermon.title)}" aria-label="Comment on sermon">☏</button><button class="mini-action share-button" data-title="${escapeHtml(sermon.title)}" aria-label="Share sermon">↗</button></div></div></div>
  </article>`;
}

function renderSermons() {
  const allSermons = [...uploadedSermons, ...sermons];
  const filtered = allSermons.map((sermon, index) => ({ sermon, index }))
    .filter(({ sermon }) => activeFilter === "All" || sermon.topic === activeFilter);
  if (reverseSermons) filtered.reverse();
  document.querySelector("#homeSermons").innerHTML = allSermons.slice(0, 3).map(sermonCard).join("");
  document.querySelector("#sermonList").innerHTML = filtered.map(({ sermon, index }) => sermonCard(sermon, index)).join("");
  updateSermonPlayerControls();
}

function updateSermonPlayerControls() {
  const player = document.querySelector("#recordedAudio");
  const activeUrl = player.dataset.audioUrl || "";
  document.querySelectorAll(".sermon-play-button").forEach((button) => {
    const isCurrent = activeUrl && button.dataset.audioUrl === activeUrl;
    button.textContent = isCurrent && !player.paused ? "Ⅱ Pause" : "▶ Play";
  });
  document.querySelectorAll(".sermon-stop-button").forEach((button) => {
    button.disabled = !activeUrl || button.dataset.audioUrl !== activeUrl;
  });
}

function stopSermonAudio() {
  const player = document.querySelector("#recordedAudio");
  player.pause();
  player.currentTime = 0;
  player.removeAttribute("src");
  delete player.dataset.audioUrl;
  player.load();
  document.querySelector("#sermonPlayerDock").hidden = true;
  document.querySelector("#sermonNowPlaying").textContent = "";
  updateSermonPlayerControls();
}

function renderShorts() {
  const allShorts = [...uploadedShorts, ...shorts];
  document.querySelector("#shortsGrid").innerHTML = allShorts.map((item) => `<article class="short-card" data-content-title="${escapeHtml(item.title)}" data-video-url="${escapeHtml(item.video_url || "")}">
    <div class="short-art ${item.art || "art-mist"}"><div class="art-illustration">${escapeHtml(item.symbol || "▷")}</div><button class="short-play" aria-label="Play ${escapeHtml(item.title)}">▶</button></div>
    <div class="short-info"><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.subtitle || item.speaker || "LIFE FOUNTAIN CHURCH")}</p><div class="short-actions"><button class="mini-action like-button" aria-label="Like video" aria-pressed="false">♡</button><button class="mini-action comment-button" data-title="${escapeHtml(item.title)}" aria-label="Comment on video">☏</button><button class="mini-action share-button" data-title="${escapeHtml(item.title)}" aria-label="Share video">↗</button></div></div>
  </article>`).join("");
}

function setLiveStreamCover(url) {
  const image = document.querySelector("#liveStreamCover");
  const liveCard = document.querySelector("#liveCard");
  liveCard.classList.toggle("has-cover", Boolean(url));
  image.hidden = !url;
  if (url) image.src = url;
  else image.removeAttribute("src");
}

function renderPastors() {
  const pastorList = document.querySelector("#pastorList");
  const select = document.querySelector("#pastorSelect");
  const count = document.querySelector("#pastorCount");
  if (count) count.textContent = String(pastors.length);
  if (pastorList) pastorList.innerHTML = pastors.length
    ? pastors.map((pastor) => `<div class="pastor-row"><span class="pastor-avatar avatar-sage">${escapeHtml(pastor.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("").toUpperCase())}</span><div><strong>${escapeHtml(pastor.name)}</strong><small>${escapeHtml(pastor.email)}</small></div><span class="pastor-access">Prayer requests</span><span class="pastor-active">Active</span></div>`).join("")
    : '<p class="empty-inbox">No pastors added yet. Add pastors here so your team can follow up on prayer requests.</p>';
  select.innerHTML = '<option value="">Pastoral care team</option>' +
    pastors.map((pastor) => `<option value="${escapeHtml(pastor.id)}">${escapeHtml(pastor.name)}</option>`).join("");
}

function renderPrayerInbox() {
  const openCount = prayerRequests.filter((request) => !request.response).length;
  const total = document.querySelector("#totalPrayerCount");
  const open = document.querySelector("#openPrayerCount");
  const count = document.querySelector("#inboxCount");
  const inbox = document.querySelector("#prayerInbox");
  if (total) total.textContent = String(prayerRequests.length);
  if (open) open.textContent = String(openCount);
  if (count) count.textContent = `${prayerRequests.length} ${prayerRequests.length === 1 ? "request" : "requests"}`;
  if (!inbox) return;
  inbox.innerHTML = prayerRequests.length
    ? prayerRequests.map((request) => `<article class="prayer-request ${request.response ? "prayer-request-answered" : ""}">
        <div class="prayer-request-heading"><div><strong>${escapeHtml(request.name || "Church member")}</strong><span>${escapeHtml(new Date(request.created_at * 1000).toLocaleString())} · To ${escapeHtml(request.pastor_name || "Pastoral care team")}</span></div><span class="request-status">${request.response ? "Answered" : "Needs a response"}</span></div>
        <p class="prayer-request-message">${escapeHtml(request.message)}</p>
        ${request.response
          ? `<div class="pastor-response"><strong>Pastoral response · ${escapeHtml(request.responder_name || "Pastor")}</strong><p>${escapeHtml(request.response)}</p></div>`
          : `<form class="prayer-response-form" data-prayer-id="${escapeHtml(request.id)}"><label for="response-${escapeHtml(request.id)}">Pastoral response</label><textarea id="response-${escapeHtml(request.id)}" name="response" rows="2" placeholder="Write a reply or note that you prayed with them..." required></textarea><button type="submit" class="button button-primary">Send response</button></form>`}
      </article>`).join("")
    : '<p class="empty-inbox">No prayer requests yet. Requests submitted from the member page will appear here.</p>';
}

function getPartOfDay(hour = new Date().getHours()) {
  if (hour < 12) return "MORNING";
  if (hour < 17) return "MIDDAY";
  return "EVENING";
}

function refreshVerses() {
  const shuffled = verses.map((verse) => ({ verse, order: Math.random() })).sort((a, b) => a.order - b.order);
  const times = ["MORNING", "MIDDAY", "EVENING"];
  const slots = times.map((time, index) => ({ ...shuffled[index].verse, time }));
  const currentTime = getPartOfDay();
  const featured = slots.find((verse) => verse.time === currentTime) || slots[0];
  document.querySelector("#dailyVerse").textContent = `“${featured.text}”`;
  document.querySelector("#verseReference").textContent = featured.reference;
  document.querySelector(".verse-time").textContent = `·  ${featured.time}`;
  document.querySelector("#verseGrid").innerHTML = slots.map((verse) => `<article class="daily-verse-card"><span class="section-kicker">${verse.time} REFLECTION</span><blockquote>“${escapeHtml(verse.text)}”</blockquote><span>${escapeHtml(verse.reference)}</span></article>`).join("");
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove("show"), 2800);
}

function openComments(title) {
  activeCommentTitle = title;
  document.querySelector("#commentHeading").textContent = "Leave a comment";
  document.querySelector("#commentContext").textContent = title;
  const saved = comments.get(title) || [];
  document.querySelector("#commentList").innerHTML = saved.length
    ? saved.map((comment) => `<p class="posted-comment">${escapeHtml(comment)}</p>`).join("")
    : '<p class="empty-comments">Be the first to share a thoughtful comment.</p>';
  document.querySelector("#commentInput").value = "";
  document.querySelector("#commentDialog").showModal();
  document.querySelector("#commentInput").focus();
}

function showPage(name) {
  if (!pageNames[name]) return;
  if (name === "studio" && !studioUnlocked) {
    pendingPage = name;
    document.querySelector("#studioUnlockMessage").textContent = "";
    document.querySelector("#studioPassword").value = "";
    if (!document.querySelector("#studioDialog").open) {
      document.querySelector("#studioDialog").showModal();
      document.querySelector("#studioPassword").focus();
    }
    return;
  }
  if (name === "admin" && currentUser?.role !== "admin") {
    pendingPage = name;
    configureAuthDialog(setupRequired ? "setup" : "login");
    if (!document.querySelector("#authDialog").open) document.querySelector("#authDialog").showModal();
    return;
  }
  if ((name === "admin" || name === "studio") && !currentUser) {
    pendingPage = name;
    configureAuthDialog(setupRequired ? "setup" : "login");
    document.querySelector("#authDialog").showModal();
    return;
  }
  document.querySelectorAll(".page-view").forEach((view) => view.classList.toggle("active", view.dataset.view === name));
  document.querySelectorAll(".nav-item[data-page]").forEach((item) => item.classList.toggle("active", item.dataset.page === name));
  document.querySelector("#breadcrumbCurrent").textContent = pageNames[name];
  document.querySelector("#sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "bible" && !bibleBooks) {
    loadBible().catch((error) => {
      document.querySelector("#bibleResultsNote").textContent = error.message;
      showToast(error.message);
    });
  }
}

function addLike(button) {
  const liked = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-pressed", String(liked));
  button.classList.toggle("liked", liked);
  button.textContent = liked ? "♥" : "♡";
}

async function shareContent(title) {
  const url = new URL(window.location.href);
  url.hash = `content-${encodeURIComponent(title.toLowerCase().replace(/[^a-z0-9]+/g, "-"))}`;
  try {
    if (navigator.share) await navigator.share({ title, url: url.href });
    else if (navigator.clipboard) {
      await navigator.clipboard.writeText(url.href);
      showToast("Share link copied to clipboard.");
    } else {
      showToast(url.href);
    }
  } catch (error) {
    if (error.name !== "AbortError") showToast("Could not share this link. Please try again.");
  }
}

async function toggleLiveListen() {
  const player = document.querySelector("#liveAudio");
  if (listenerActive) {
    listenerActive = false;
    clearTimeout(listenerTimer);
    player.pause();
    player.hidden = true;
    player.controls = false;
    document.querySelector("#listenLive").textContent = "▶";
    return;
  }
  try {
    const result = await apiRequest("/api/stream");
    if (!result.stream) {
      showToast("There is no live audio stream at the moment.");
      return;
    }
    if (!window.MediaSource || !MediaSource.isTypeSupported(result.stream.mime_type)) {
      showToast("Live audio playback is not supported by this browser.");
      return;
    }
    listenerActive = true;
    listenerAutoplayBlocked = false;
    listenerStartedPlayback = false;
    pendingListenerStreamId = result.stream.id;
    listenerSequence = -1;
    queuedAudioSegments = [];
    mediaSource = new MediaSource();
    sourceBuffer = null;
    player.src = URL.createObjectURL(mediaSource);
    player.hidden = false;
    player.style.display = "block";
    player.style.width = "min(360px, 100%)";
    player.style.height = "34px";
    player.style.marginTop = "8px";
    player.controls = true;
    document.querySelector("#listenLive").textContent = "Ⅱ";
    mediaSource.addEventListener("sourceopen", () => {
      if (!listenerActive || !mediaSource || mediaSource.readyState !== "open") return;
      try {
        sourceBuffer = mediaSource.addSourceBuffer(result.stream.mime_type);
        sourceBuffer.mode = "sequence";
        sourceBuffer.addEventListener("updateend", () => {
          const bufferedAhead = sourceBuffer.buffered.length
            ? sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1) - player.currentTime
            : 0;
          if (
            !listenerStartedPlayback &&
            !listenerAutoplayBlocked &&
            bufferedAhead >= LIVE_AUDIO_STARTUP_BUFFER_SECONDS
          ) {
            player.play().then(() => {
              listenerStartedPlayback = true;
            }).catch(() => {
              listenerAutoplayBlocked = true;
              document.querySelector("#liveDescription").textContent =
                "Live audio is buffered. Press play on the audio controls to listen.";
            });
          }
          appendNextAudioSegment();
        });
        document.querySelector("#liveDescription").textContent = "Buffering live audio…";
        pollLiveAudio();
      } catch (error) {
        listenerActive = false;
        showToast(`Could not open live audio: ${error.message}`);
      }
    }, { once: true });
  } catch (error) {
    showToast(`Could not join live audio: ${error.message}`);
  }
}

function appendNextAudioSegment() {
  if (!listenerActive || !sourceBuffer || sourceBuffer.updating || !queuedAudioSegments.length) return;
  try {
    sourceBuffer.appendBuffer(queuedAudioSegments.shift());
  } catch (error) {
    showToast(`Live audio playback stopped: ${error.message}`);
    listenerActive = false;
    clearTimeout(listenerTimer);
  }
}

async function pollLiveAudio() {
  if (!listenerActive || !pendingListenerStreamId) return;
  try {
    const result = await apiRequest(
      `/api/streams/${pendingListenerStreamId}/chunks?after=${listenerSequence}`
    );
    if (!result.live) {
      document.querySelector("#liveDescription").textContent = "The live stream has ended.";
      listenerActive = false;
      clearTimeout(listenerTimer);
      return;
    }
    const sequences = result.sequences.slice(0, LIVE_AUDIO_DOWNLOAD_BATCH_SIZE);
    const segments = await Promise.all(sequences.map(async (sequence) => {
      const response = await fetch(`/api/streams/${pendingListenerStreamId}/chunks/${sequence}`, {
        credentials: "same-origin",
        cache: "no-store"
      });
      if (!response.ok) throw new Error(`Could not download live audio (${response.status}).`);
      return response.arrayBuffer();
    }));
    for (let index = 0; index < sequences.length; index += 1) {
      queuedAudioSegments.push(segments[index]);
      const sequence = sequences[index];
      listenerSequence = sequence;
      appendNextAudioSegment();
    }
    const hasBacklog = result.sequences.length > sequences.length;
    listenerTimer = window.setTimeout(
      pollLiveAudio,
      hasBacklog ? 0 : LIVE_AUDIO_POLL_INTERVAL_MS
    );
  } catch (error) {
    listenerActive = false;
    document.querySelector("#listenLive").textContent = "▶";
    showToast(`Live audio connection lost: ${error.message}`);
  }
}

async function refreshLiveStream() {
  const result = await apiRequest("/api/stream");
  const stream = result.stream;
  const pill = document.querySelector("#homeStreamStatus");
  const listenerCount = document.querySelector("#homeListenerCount");
  const title = document.querySelector("#homeStreamTitle");
  const button = document.querySelector("#listenLive");
  const status = document.querySelector("#streamStatus");
  const studioTitle = document.querySelector("#streamTitle");
  const studioDescription = document.querySelector("#streamDescription");
  if (stream) {
    pill.innerHTML = "<span></span> LIVE AUDIO";
    listenerCount.textContent = "Audio broadcast live";
    title.textContent = stream.title;
    setLiveStreamCover(stream.cover_url);
    document.querySelector("#liveButtonText").textContent = "Listen to the live audio";
    document.querySelector("#liveDescription").textContent = "Live from Life Fountain Church.";
    button.disabled = false;
    if (!liveRecording) {
      status.textContent = "● LIVE";
      status.classList.add("live-status");
      studioTitle.textContent = stream.title;
      studioDescription.textContent = "A live broadcast is active.";
    }
    activeStream = stream;
  } else {
    pill.innerHTML = "<span></span> OFF AIR";
    listenerCount.textContent = "No active broadcast";
    title.innerHTML = "Join us for<br>live audio.";
    setLiveStreamCover(null);
    if (!listenerActive) {
      document.querySelector("#liveButtonText").textContent = "No live audio right now";
      document.querySelector("#liveDescription").textContent = "When the church is live, join the stream here.";
    }
    button.disabled = false;
    if (!liveRecording) {
      status.textContent = "● OFFLINE";
      status.classList.remove("live-status");
      studioTitle.textContent = "Ready when you are";
      studioDescription.textContent = currentUser ? "Allow microphone access to start." : "Sign in as a pastor or admin to start streaming.";
    }
    activeStream = null;
  }
}

async function refreshRecordings() {
  const recordings = await apiRequest("/api/recordings");
  const list = document.querySelector("#recordingsList");
  if (!list) return;
  list.innerHTML = recordings.length
    ? recordings.map((recording) => `<article class="recording-row">${recording.cover_url ? `<img class="recording-cover" src="${escapeHtml(recording.cover_url)}" alt="${escapeHtml(recording.title)} wallpaper">` : ""}<strong>${escapeHtml(recording.title)}</strong><time>${new Date(recording.ended_at * 1000).toLocaleString()}</time><audio controls preload="none" src="/api/recordings/${encodeURIComponent(recording.id)}"></audio>${currentUser?.role === "admin" ? `<button class="edit-sermon-button" data-edit-url="/api/recordings/${encodeURIComponent(recording.id)}/title" data-edit-title="${escapeHtml(recording.title)}">Edit sermon name</button><button class="delete-sermon-button" data-delete-url="/api/recordings/${encodeURIComponent(recording.id)}" data-delete-title="${escapeHtml(recording.title)}">Delete sermon</button>` : ""}</article>`).join("")
    : '<p class="empty-inbox">No saved streams yet. Completed audio streams will appear here.</p>';
  if (recordings) await refreshMedia();
}

async function startRecordingCapture(microphone) {
  const AudioContextType = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextType) throw new Error("Audio recording is not supported in this browser.");
  recordingContext = new AudioContextType();
  await recordingContext.resume();
  recordingSampleRate = Math.min(16000, recordingContext.sampleRate);
  recordingResamplePhase = 0;
  recordingChunks = [];
  recordingSource = recordingContext.createMediaStreamSource(microphone);
  recordingProcessor = recordingContext.createScriptProcessor(4096, 1, 1);
  const inputSampleRate = recordingContext.sampleRate;
  const silentOutput = recordingContext.createGain();
  silentOutput.gain.value = 0;
  recordingProcessor.addEventListener("audioprocess", (event) => {
    const input = event.inputBuffer.getChannelData(0);
    const ratio = inputSampleRate / recordingSampleRate;
    const samples = new Int16Array(Math.ceil(input.length / ratio) + 1);
    let outputIndex = 0;
    let inputPosition = recordingResamplePhase;
    for (; inputPosition < input.length; inputPosition += ratio) {
      const sample = Math.max(-1, Math.min(1, input[Math.floor(inputPosition)]));
      samples[outputIndex++] = sample < 0 ? sample * 32768 : sample * 32767;
    }
    recordingResamplePhase = inputPosition - input.length;
    if (outputIndex) recordingChunks.push(samples.slice(0, outputIndex));
    event.outputBuffer.getChannelData(0).fill(0);
  });
  recordingSource.connect(recordingProcessor);
  recordingProcessor.connect(silentOutput);
  silentOutput.connect(recordingContext.destination);
}

async function stopRecordingCapture() {
  if (!recordingContext) throw new Error("The audio recording could not be finalized.");
  recordingProcessor?.disconnect();
  recordingSource?.disconnect();
  const context = recordingContext;
  recordingProcessor = null;
  recordingSource = null;
  await context.close();
  recordingContext = null;
  const byteLength = recordingChunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  if (!byteLength) throw new Error("No recording audio was captured.");
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const writeText = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };
  writeText(0, "RIFF");
  view.setUint32(4, byteLength + 36, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, recordingSampleRate, true);
  view.setUint32(28, recordingSampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, byteLength, true);
  const parts = [header];
  for (const chunk of recordingChunks) parts.push(chunk);
  const recording = new Blob(parts, { type: "audio/wav" });
  recordingChunks = [];
  return recording;
}

async function cancelRecordingCapture() {
  recordingProcessor?.disconnect();
  recordingSource?.disconnect();
  const context = recordingContext;
  recordingProcessor = null;
  recordingSource = null;
  recordingContext = null;
  recordingChunks = [];
  if (context && context.state !== "closed") await context.close();
}

async function toggleRecording() {
  const streamButton = document.querySelector("#streamButton");
  const status = document.querySelector("#streamStatus");
  const streamTitle = document.querySelector("#streamTitle");
  const streamDescription = document.querySelector("#streamDescription");
  if (liveRecording) {
    streamButton.disabled = true;
    try {
      if (activeRecorder && activeRecorder.state !== "inactive") {
        await new Promise((resolve) => {
          activeRecorder.addEventListener("stop", resolve, { once: true });
          activeRecorder.stop();
        });
      }
      const recording = await stopRecordingCapture();
      activeRecorder?.stream.getTracks().forEach((track) => track.stop());
      await uploadQueue;
      if (uploadError) throw uploadError;
      await apiRequest(`/api/streams/${activeStream.id}/stop`, {
        method: "POST",
        headers: { "Content-Type": recording.type },
        body: recording
      });
      liveRecording = false;
      activeRecorder = null;
      activeStream = null;
      streamButton.textContent = "●  Start audio stream";
      streamButton.classList.remove("recording-button");
      status.classList.remove("live-status");
      streamTitle.textContent = "Recording saved";
      streamDescription.textContent = "Your stream recording is saved on the server.";
      await refreshLiveStream();
      await refreshRecordings();
      document.querySelector("#streamCoverInput").value = "";
      document.querySelector("#streamCoverLabel").textContent = "Choose live stream wallpaper (optional)";
      showToast("Live stream ended and saved to recordings.");
    } catch (error) {
      showToast(`Could not finish saving the stream: ${error.message}`);
    } finally {
      if (recordingContext) await cancelRecordingCapture();
      streamButton.disabled = false;
    }
    return;
  }
  if (!currentUser) {
    pendingPage = "studio";
    configureAuthDialog("login");
    document.querySelector("#authDialog").showModal();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("Audio recording is not supported in this browser.");
    return;
  }
  const streamName = document.querySelector("#streamName").value.trim();
  if (!streamName) {
    showToast("Enter a title for this live stream.");
    document.querySelector("#streamName").focus();
    return;
  }
  streamButton.disabled = true;
  try {
    const microphone = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    if (!mimeType) throw new Error("Your browser does not support a server-compatible audio format.");
    activeRecorder = new MediaRecorder(microphone, { mimeType });
    try {
      await startRecordingCapture(microphone);
      const formData = new FormData();
      formData.set("title", streamName);
      formData.set("mime_type", mimeType);
      const wallpaper = document.querySelector("#streamCoverInput").files[0];
      if (wallpaper) formData.set("cover", wallpaper);
      activeStream = await apiRequest("/api/streams", {
        method: "POST",
        body: formData
      });
    } catch (error) {
      await cancelRecordingCapture();
      microphone.getTracks().forEach((track) => track.stop());
      throw error;
    }
    uploadSequence = 0;
    uploadQueue = Promise.resolve();
    uploadError = null;
    activeRecorder.addEventListener("dataavailable", (event) => {
      if (!event.data.size) return;
      const sequence = uploadSequence++;
      uploadQueue = uploadQueue.then(async () => {
        const response = await fetch(`/api/streams/${activeStream.id}/chunks`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": event.data.type || mimeType, "X-Audio-Sequence": String(sequence) },
          body: event.data
        });
        if (!response.ok) {
          const failure = await response.json().catch(() => ({}));
          throw new Error(failure.error || `Audio upload failed (${response.status}).`);
        }
        streamDescription.textContent = `Live audio connected · ${sequence + 1} segment${sequence ? "s" : ""} sent.`;
      }).catch((error) => {
        uploadError = error;
        showToast(`Stream upload failed: ${error.message}`);
      });
    });
    activeRecorder.start(500);
    liveRecording = true;
    streamButton.textContent = "■  Stop stream and save";
    streamButton.classList.add("recording-button");
    streamButton.disabled = false;
    status.textContent = "● LIVE";
    status.classList.add("live-status");
    streamTitle.textContent = streamName;
    streamDescription.textContent = "Microphone is live. Audio segments are being sent to the server for listeners.";
    document.querySelector("#homeStreamStatus").innerHTML = "<span></span> LIVE AUDIO";
    document.querySelector("#homeListenerCount").textContent = "Audio broadcast live";
    document.querySelector("#homeStreamTitle").textContent = streamName;
    setLiveStreamCover(activeStream.cover_url);
    document.querySelector("#liveButtonText").textContent = "Listen to the live audio";
    document.querySelector("#liveDescription").textContent = "Live from Life Fountain Church.";
    await refreshRecordings();
  } catch (error) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError") {
      showToast("Microphone access was denied. Allow microphone access to record.");
    } else {
      showToast(`Could not start the live stream: ${error.message}`);
    }
  } finally {
    streamButton.disabled = false;
  }
}

function formatUploadSize(size) {
  const units = ["B", "MB", "GB", "TB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex ? 1 : 0)} ${units[unitIndex]}`;
}

async function uploadLargeVideoSermon(file, title, cover, feedback) {
  const storageKey = `lfc-sermon-upload:${JSON.stringify([file.name, file.size, file.lastModified])}`;
  let uploadId = "";
  let uploadedBytes = 0;
  let upload;
  try {
    const savedUploadId = sessionStorage.getItem(storageKey);
    if (savedUploadId) {
      try {
        upload = await apiRequest(`/api/sermon-uploads/${encodeURIComponent(savedUploadId)}`);
        if (upload.file_size === file.size) {
          uploadId = upload.id;
          uploadedBytes = upload.uploaded_bytes;
          feedback.textContent = `Resuming upload at ${Math.floor(uploadedBytes / file.size * 100)}%…`;
        } else {
          sessionStorage.removeItem(storageKey);
        }
      } catch (error) {
        if (!/404|not found/i.test(error.message)) throw error;
        sessionStorage.removeItem(storageKey);
      }
    }
    if (!uploadId) {
      upload = await apiRequest("/api/sermon-uploads", {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          mime_type: file.type || "video/mp4",
          file_size: file.size,
          title
        })
      });
      uploadId = upload.id;
      uploadedBytes = upload.uploaded_bytes;
      sessionStorage.setItem(storageKey, uploadId);
    }

    while (uploadedBytes < file.size) {
      const start = uploadedBytes;
      const end = Math.min(file.size, start + upload.chunk_size);
      let chunkAccepted = false;
      let lastError;
      for (let attempt = 0; attempt < 3 && !chunkAccepted; attempt += 1) {
        try {
          const result = await apiRequest(`/api/sermon-uploads/${encodeURIComponent(uploadId)}/chunk`, {
            method: "POST",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-Upload-Offset": String(start)
            },
            body: file.slice(start, end)
          });
          uploadedBytes = result.uploaded_bytes;
          chunkAccepted = true;
        } catch (error) {
          lastError = error;
          const status = await apiRequest(`/api/sermon-uploads/${encodeURIComponent(uploadId)}`);
          uploadedBytes = status.uploaded_bytes;
          if (uploadedBytes >= end) chunkAccepted = true;
          else if (uploadedBytes !== start || attempt === 2) throw error;
          else await new Promise((resolve) => window.setTimeout(resolve, 750 * (attempt + 1)));
        }
      }
      if (!chunkAccepted) throw lastError || new Error("The video upload chunk failed.");
      const percentage = Math.min(100, Math.floor(uploadedBytes / file.size * 100));
      feedback.textContent =
        `Uploading ${file.name} · ${percentage}% (${formatUploadSize(uploadedBytes)} of ${formatUploadSize(file.size)})`;
    }

    if (cover) {
      const coverForm = new FormData();
      coverForm.set("file", cover);
      await apiRequest(`/api/sermon-uploads/${encodeURIComponent(uploadId)}/cover`, {
        method: "POST",
        body: coverForm
      });
    }
    feedback.textContent = "Upload complete. Converting the video to sermon audio; large files may take a long time…";
    const sermon = await apiRequest(`/api/sermon-uploads/${encodeURIComponent(uploadId)}/complete`, {
      method: "POST",
      body: JSON.stringify({})
    });
    sessionStorage.removeItem(storageKey);
    return sermon;
  } catch (error) {
    const percentage = file.size ? Math.floor(uploadedBytes / file.size * 100) : 0;
    if (uploadId) {
      feedback.textContent =
        `${error.message} Your upload is saved at ${percentage}%; reselect this same video to resume or retry conversion.`;
    }
    throw error;
  }
}

function setupUpload(inputId, kind, description) {
  const input = document.querySelector(inputId);
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file) return;
    const feedback = document.querySelector("#uploadFeedback");
    if (!currentUser) {
      feedback.textContent = "Sign in as a pastor or admin before uploading media.";
      input.value = "";
      configureAuthDialog("login");
      document.querySelector("#authDialog").showModal();
      return;
    }
    const audioUpload = kind === "audio-sermon";
    if (audioUpload ? !file.type.startsWith("audio/") : !file.type.startsWith("video/")) {
      feedback.textContent = audioUpload ? "Choose an audio file to continue." : "Choose a video file to continue.";
      input.value = "";
      return;
    }
    const maxFileSize = kind === "sermon" ? 20 * 1024 * 1024 * 1024 : 100 * 1024 * 1024;
    if (file.size > maxFileSize) {
      feedback.textContent = kind === "sermon"
        ? "Video sermons can be up to 20 GB."
        : "That file is over the 100 MB upload limit.";
      input.value = "";
      return;
    }
    const customTitle = kind !== "short" ? document.querySelector("#sermonTitle").value.trim() : "";
    const title = customTitle || file.name.replace(/\.[^.]+$/, "").slice(0, 120);
    const uploadButton = document.querySelector(
      kind === "sermon" ? "#sermonUploadTrigger" :
        audioUpload ? "#audioSermonUploadTrigger" : "#shortUploadTrigger"
    );
    uploadButton.disabled = true;
    let uploadRequest;
    if (kind === "sermon") {
      uploadRequest = uploadLargeVideoSermon(file, title, pendingSermonCover, feedback);
    } else {
      const formData = new FormData();
      formData.set("kind", kind);
      formData.set("title", title);
      formData.set("file", file);
      if (audioUpload && pendingSermonCover) formData.set("cover", pendingSermonCover);
      feedback.textContent = `${audioUpload ? "Uploading" : "Uploading and processing"} ${file.name}…`;
      uploadRequest = apiRequest("/api/media", { method: "POST", body: formData });
    }
    uploadRequest.then(async (item) => {
      feedback.textContent = `${description} "${item.title}" uploaded and published.`;
      if (kind !== "short") {
        pendingSermonCover = null;
        document.querySelector("#sermonTitle").value = "";
        document.querySelector("#sermonCoverInput").value = "";
        document.querySelector("#coverUploadLabel").textContent = "Choose sermon wallpaper (optional)";
      }
      await refreshMedia();
      showToast(kind === "sermon" ? "Video converted to sermon audio and published." :
        audioUpload ? "Audio sermon uploaded and published." : "Short video uploaded and published.");
    }).catch((error) => {
      if (kind !== "sermon") feedback.textContent = error.message;
      showToast(error.message);
    }).finally(() => {
      input.value = "";
      uploadButton.disabled = false;
    });
  });
}

function updateSearch(query) {
  const term = query.trim().toLowerCase();
  const sermonNote = document.querySelector("#sermonSearchNote");
  const shortNote = document.querySelector("#shortSearchNote");
  if (!term) {
    sermonNote.hidden = true;
    shortNote.hidden = true;
    renderSermons();
    renderShorts();
    return;
  }
  const matchingSermons = [...uploadedSermons, ...sermons].filter((sermon) =>
    `${sermon.title} ${sermon.speaker} ${sermon.topic}`.toLowerCase().includes(term));
  const matchingShorts = [...uploadedShorts, ...shorts].filter((video) =>
    `${video.title} ${video.subtitle}`.toLowerCase().includes(term));
  const targetPage = matchingSermons.length ? "sermons" : "shorts";
  if (!document.querySelector(`[data-view="${targetPage}"]`).classList.contains("active")) showPage(targetPage);
  activeFilter = "All";
  document.querySelectorAll(".filter-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.filter === "All"));
  renderSermons();
  renderShorts();
  document.querySelectorAll("#sermonList .sermon-card").forEach((card) => {
    card.hidden = !card.dataset.contentTitle.toLowerCase().includes(term) &&
      !card.textContent.toLowerCase().includes(term);
  });
  document.querySelectorAll("#shortsGrid .short-card").forEach((card) => {
    card.hidden = !card.dataset.contentTitle.toLowerCase().includes(term) &&
      !card.textContent.toLowerCase().includes(term);
  });
  sermonNote.hidden = false;
  shortNote.hidden = false;
  const escapedTerm = `“${query.trim()}”`;
  sermonNote.textContent = matchingSermons.length
    ? `${matchingSermons.length} sermon${matchingSermons.length === 1 ? "" : "s"} found for ${escapedTerm}.`
    : `No sermons found for ${escapedTerm}.`;
  shortNote.textContent = matchingShorts.length
    ? `${matchingShorts.length} video${matchingShorts.length === 1 ? "" : "s"} found for ${escapedTerm}.`
    : `No short videos found for ${escapedTerm}.`;
}

function openSharedContent() {
  const slug = window.location.hash.slice(1).replace(/^content-/, "");
  if (slug === "studio" || slug === "admin") return;
  if (!slug) return;
  const sermon = [...uploadedSermons, ...sermons].find((item) => item.title.toLowerCase().replace(/[^a-z0-9]+/g, "-") === slug);
  const short = [...uploadedShorts, ...shorts].find((item) => item.title.toLowerCase().replace(/[^a-z0-9]+/g, "-") === slug);
  const isLive = "wednesday-night-worship" === slug;
  document.querySelector("#searchInput").value = "";
  updateSearch("");
  if (sermon) showPage("sermons");
  else if (short) showPage("shorts");
  else if (isLive) showPage("home");
  else return;
  const contentList = sermon ? "#sermonList" : short ? "#shortsGrid" : null;
  const target = contentList
    ? document.querySelector(`${contentList} [data-content-title="${(sermon || short).title}"]`)
    : isLive ? document.querySelector("#liveCard") : null;
  target?.scrollIntoView({ behavior: "smooth", block: "center" });
}

document.addEventListener("click", (event) => {
  const pageButton = event.target.closest("[data-page]");
  if (pageButton) {
    event.preventDefault();
    showPage(pageButton.dataset.page);
    return;
  }
  const filter = event.target.closest("[data-filter]");
  if (filter) {
    activeFilter = filter.dataset.filter;
    document.querySelectorAll(".filter-tab").forEach((tab) => tab.classList.toggle("active", tab === filter));
    renderSermons();
    return;
  }
  const like = event.target.closest(".like-button");
  if (like) {
    addLike(like);
    return;
  }
  const share = event.target.closest(".share-button");
  if (share) {
    shareContent(share.dataset.title);
    return;
  }
  const comment = event.target.closest(".comment-button");
  if (comment) {
    openComments(comment.dataset.title);
    return;
  }
  const deleteSermon = event.target.closest(".delete-sermon-button");
  const editSermon = event.target.closest(".edit-sermon-button");
  if (editSermon) {
    if (currentUser?.role !== "admin") {
      showToast("Only an administrator can edit sermon names.");
      return;
    }
    const updatedTitle = window.prompt("Enter a new name for this sermon:", editSermon.dataset.editTitle);
    if (updatedTitle === null) return;
    const title = updatedTitle.trim();
    if (!title || title.length > 120) {
      showToast("Enter a sermon name between 1 and 120 characters.");
      return;
    }
    editSermon.disabled = true;
    apiRequest(editSermon.dataset.editUrl, {
      method: "POST",
      body: JSON.stringify({ title })
    }).then(async () => {
      await refreshMedia();
      await refreshRecordings();
      showToast("Sermon name updated.");
    }).catch((error) => {
      editSermon.disabled = false;
      showToast(`Could not update sermon name: ${error.message}`);
    });
    return;
  }
  if (deleteSermon) {
    if (currentUser?.role !== "admin") {
      showToast("Only an administrator can delete sermons.");
      return;
    }
    if (!window.confirm(`Permanently delete "${deleteSermon.dataset.deleteTitle}"? This also deletes its audio and wallpaper.`)) return;
    deleteSermon.disabled = true;
    apiRequest(deleteSermon.dataset.deleteUrl, { method: "DELETE" })
      .then(async () => {
        await refreshRecordings();
        showToast("Sermon and its saved files were deleted.");
      })
      .catch((error) => {
        deleteSermon.disabled = false;
        showToast(`Could not delete the sermon: ${error.message}`);
      });
    return;
  }
  const stopSermon = event.target.closest("[data-sermon-stop]");
  if (stopSermon) {
    const player = document.querySelector("#recordedAudio");
    if (player.dataset.audioUrl === stopSermon.dataset.audioUrl) stopSermonAudio();
    return;
  }
  const play = event.target.closest("[data-play], .short-play");
  if (play?.matches("[data-play]")) {
    const audioUrl = play.dataset.audioUrl;
    if (!audioUrl) {
      showToast("This is sample sermon content; upload a sermon in the Streaming studio to play audio.");
      return;
    }
    const player = document.querySelector("#recordedAudio");
    if (player.dataset.audioUrl === audioUrl && !player.paused) {
      player.pause();
      return;
    }
    player.src = audioUrl;
    player.dataset.audioUrl = audioUrl;
    document.querySelector("#sermonNowPlaying").textContent =
      play.closest(".sermon-card")?.dataset.contentTitle || "Sermon";
    document.querySelector("#sermonPlayerDock").hidden = false;
    player.hidden = false;
    player.play().then(async () => {
      updateSermonPlayerControls();
      const card = play.closest(".sermon-card");
      const count = card?.querySelector(".sermon-view-count");
      if (count?.dataset.viewUrl) {
        try {
          const result = await apiRequest(count.dataset.viewUrl, { method: "POST", body: "{}" });
          document.querySelectorAll(".sermon-view-count").forEach((viewCount) => {
            if (viewCount.dataset.viewUrl === count.dataset.viewUrl) {
              viewCount.textContent = `${result.view_count} ${result.view_count === 1 ? "view" : "views"}`;
            }
          });
        } catch (error) {
          console.error("Could not record sermon playback view:", error);
        }
      }
    }).catch((error) => {
      showToast(`Could not play sermon audio: ${error.message}`);
      updateSermonPlayerControls();
    });
    return;
  }
  if (play?.matches(".short-play")) {
    const card = play.closest(".short-card");
    const videoUrl = card.dataset.videoUrl;
    if (!videoUrl) {
      showToast("This is sample video content; upload a short video in the Streaming studio to play it.");
      return;
    }
    const art = card.querySelector(".short-art");
    const video = document.createElement("video");
    video.controls = true;
    video.playsInline = true;
    video.src = videoUrl;
    video.className = "uploaded-short-player";
    video.addEventListener("error", () => showToast("Could not play this video."));
    art.replaceChildren(video);
    video.play().catch((error) => showToast(`Could not play this video: ${error.message}`));
  }
});

document.querySelector("#prayerForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const formData = new FormData(form);
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  apiRequest("/api/prayers", {
    method: "POST",
    body: JSON.stringify({
      name: String(formData.get("name") || "").trim(),
      pastor_id: String(formData.get("pastor") || ""),
      message: String(formData.get("message") || "").trim()
    })
  }).then(async () => {
    document.querySelector("#prayerMessageStatus").textContent = "Your prayer request was sent securely to the pastoral team.";
    form.reset();
    if (currentUser) await refreshStaffData();
  }).catch((error) => {
    document.querySelector("#prayerMessageStatus").textContent = error.message;
  }).finally(() => {
    submit.disabled = false;
  });
});

document.querySelector("#pastorForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const formData = new FormData(form);
  const name = String(formData.get("name")).trim();
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  apiRequest("/api/pastors", {
    method: "POST",
    body: JSON.stringify({
      name,
      email: String(formData.get("email")).trim(),
      password: String(formData.get("password"))
    })
  }).then(async () => {
    form.reset();
    form.hidden = true;
    await loadPublicPastors();
    await refreshStaffData();
    showToast(`${name} added. Share the temporary password with the pastor securely.`);
  }).catch((error) => {
    showToast(error.message);
  }).finally(() => {
    submit.disabled = false;
  });
});

document.addEventListener("submit", (event) => {
  const responseForm = event.target.closest(".prayer-response-form");
  if (!responseForm) return;
  event.preventDefault();
  if (!responseForm.reportValidity()) return;
  const response = new FormData(responseForm).get("response").toString().trim();
  apiRequest(`/api/prayers/${encodeURIComponent(responseForm.dataset.prayerId)}/response`, {
    method: "POST",
    body: JSON.stringify({ response })
  }).then(async () => {
    await refreshStaffData();
    showToast("Pastoral response saved.");
  }).catch((error) => showToast(error.message));
});

document.querySelector("#commentForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const comment = document.querySelector("#commentInput").value.trim();
  if (!comment) return;
  comments.set(activeCommentTitle, [...(comments.get(activeCommentTitle) || []), comment]);
  document.querySelector("#commentDialog").close();
  showToast("Your comment was added to this preview.");
});
document.querySelector(".comment-close").addEventListener("click", () => document.querySelector("#commentDialog").close());
document.querySelector("#listenLive").addEventListener("click", toggleLiveListen);
document.querySelector("#streamButton").addEventListener("click", toggleRecording);
document.querySelector("#streamName").addEventListener("input", (event) => {
  document.querySelector("#streamTitle").textContent = event.currentTarget.value.trim() || "Your live stream title";
});
document.querySelector("#streamCoverInput").addEventListener("change", (event) => {
  const file = event.currentTarget.files[0];
  document.querySelector("#streamCoverLabel").textContent = file
    ? file.name
    : "Choose live stream wallpaper (optional)";
});
document.querySelector("#accountButton").addEventListener("click", openStaffAuth);
document.querySelector("#authCancel").addEventListener("click", () => document.querySelector("#authDialog").close());
document.querySelector("#studioUnlockCancel").addEventListener("click", () => {
  document.querySelector("#studioDialog").close();
  pendingPage = null;
});
document.querySelector("#studioUnlockForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const submit = document.querySelector("#studioUnlockSubmit");
  const message = document.querySelector("#studioUnlockMessage");
  submit.disabled = true;
  apiRequest("/api/studio/unlock", {
    method: "POST",
    body: JSON.stringify({ password: String(new FormData(form).get("password") || "") })
  }).then(() => {
    studioUnlocked = true;
    document.querySelector("#studioDialog").close();
    form.reset();
    const destination = pendingPage || "studio";
    pendingPage = null;
    showPage(destination);
    showToast("Streaming studio unlocked.");
  }).catch((error) => {
    message.textContent = error.message;
  }).finally(() => {
    submit.disabled = false;
  });
});
document.querySelector("#authForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const formData = new FormData(form);
  const mode = form.dataset.mode;
  const endpoint = mode === "setup" ? "/api/setup" : "/api/login";
  const submit = document.querySelector("#authSubmit");
  const message = document.querySelector("#authMessage");
  submit.disabled = true;
  apiRequest(endpoint, {
    method: "POST",
    body: JSON.stringify({
      name: String(formData.get("name") || "").trim(),
      email: String(formData.get("email") || "").trim(),
      password: String(formData.get("password") || ""),
      setup_code: String(formData.get("setup_code") || "").trim()
    })
  }).then(async (result) => {
    setupRequired = false;
    setStaffAccess(result.user);
    document.querySelector("#authDialog").close();
    form.reset();
    await loadPublicPastors();
    await refreshStaffData();
    if (result.user.role === "admin") await loadChurchProfile();
    await loadAnnouncements();
    await refreshRecordings();
    await refreshLiveStream();
    const destination = pendingPage || (result.user.role === "admin" ? "admin" : "studio");
    pendingPage = null;
    showPage(destination);
    showToast(`Signed in as ${result.user.name}.`);
  }).catch((error) => {
    message.textContent = error.message;
  }).finally(() => {
    submit.disabled = false;
  });
});
  document.querySelector("#churchProfileForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const latitudeValue = String(data.get("latitude") || "").trim();
    const longitudeValue = String(data.get("longitude") || "").trim();
    const message = document.querySelector("#churchProfileMessage");
    const submit = form.querySelector('button[type="submit"]');
    if ((latitudeValue && !longitudeValue) || (!latitudeValue && longitudeValue)) {
      message.textContent = "Enter both map coordinates to place a pin.";
      return;
    }
    submit.disabled = true;
    message.textContent = "";
    apiRequest("/api/church-profile", {
      method: "POST",
      body: JSON.stringify({
        name: String(data.get("name") || "").trim(),
        leader: String(data.get("leader") || "").trim(),
        phone: String(data.get("phone") || "").trim(),
        email: String(data.get("email") || "").trim(),
        address: String(data.get("address") || "").trim(),
        about: String(data.get("about") || "").trim(),
        latitude: latitudeValue || null,
        longitude: longitudeValue || null
      })
    }).then(async () => {
      await loadChurchProfile();
      message.textContent = "Church details saved and published.";
    }).catch((error) => {
      message.textContent = error.message;
    }).finally(() => {
      submit.disabled = false;
    });
  });
  document.querySelector("#churchLogoInput").addEventListener("change", (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      event.currentTarget.value = "";
      document.querySelector("#churchLogoMessage").textContent = "Choose a JPG, PNG, or WebP logo smaller than 2 MB.";
      return;
    }
    document.querySelector("#churchLogoLabel").textContent = file.name;
    const preview = document.querySelector("#logoPreview");
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
    document.querySelector("#logoPreviewFallback").hidden = true;
    document.querySelector("#churchLogoMessage").textContent = "";
  });
  document.querySelector("#churchLogoForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = document.querySelector("#churchLogoInput").files?.[0];
    const message = document.querySelector("#churchLogoMessage");
    const submit = document.querySelector("#saveChurchLogo");
    if (!file) {
      message.textContent = "Choose a JPG, PNG, or WebP logo first.";
      return;
    }
    const payload = new FormData();
    payload.set("file", file);
    submit.disabled = true;
    message.textContent = "";
    try {
      await apiRequest("/api/church-logo", { method: "POST", body: payload });
      await loadChurchProfile();
      form.reset();
      document.querySelector("#churchLogoLabel").textContent = "Choose church logo";
      message.textContent = "Church logo saved and applied to the website.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  document.querySelector("#removeChurchLogo").addEventListener("click", async () => {
    const button = document.querySelector("#removeChurchLogo");
    const message = document.querySelector("#churchLogoMessage");
    button.disabled = true;
    try {
      await apiRequest("/api/church-logo", { method: "DELETE" });
      await loadChurchProfile();
      message.textContent = "Church logo removed.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  document.querySelector("#announcementForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const submit = form.querySelector('button[type="submit"]');
    const message = document.querySelector("#announcementMessageStatus");
    submit.disabled = true;
    message.textContent = "";
    try {
      const item = await apiRequest("/api/announcements", {
        method: "POST",
        body: JSON.stringify({
          title: String(data.get("title") || "").trim(),
          message: String(data.get("message") || "").trim(),
          audience: String(data.get("audience") || "")
        })
      });
      announcements.unshift(item);
      renderAnnouncements();
      form.reset();
      message.textContent = "Announcement published.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  document.querySelector("#verseForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const message = document.querySelector("#verseFormMessage");
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    message.textContent = "";
    apiRequest("/api/verses", {
      method: "POST",
      body: JSON.stringify({
        text: String(data.get("text") || "").trim(),
        reference: String(data.get("reference") || "").trim()
      })
    }).then((verse) => {
      churchVerses.unshift(verse);
      verses.push(verse);
      renderChurchVerseManagement();
      refreshVerses();
      form.reset();
      message.textContent = "Verse added to the church verse library and daily rotation.";
    }).catch((error) => {
      message.textContent = error.message;
    }).finally(() => {
      submit.disabled = false;
    });
  });
  document.querySelector("#adminVerseList").addEventListener("click", (event) => {
    const button = event.target.closest(".delete-church-verse");
    if (!button || currentUser?.role !== "admin") return;
    if (!window.confirm(`Delete ${button.dataset.verseReference} from the church verse library?`)) return;
    button.disabled = true;
    apiRequest(`/api/verses/${encodeURIComponent(button.dataset.verseId)}`, { method: "DELETE" })
      .then(() => {
        churchVerses = churchVerses.filter((verse) => verse.id !== button.dataset.verseId);
        const index = verses.findIndex((verse) => verse.id === button.dataset.verseId);
        if (index >= 0) verses.splice(index, 1);
        renderChurchVerseManagement();
        refreshVerses();
        showToast("Church verse deleted.");
      })
      .catch((error) => {
        button.disabled = false;
        showToast(`Could not delete the verse: ${error.message}`);
      });
  });
  document.querySelector("#bibleBook").addEventListener("change", updateBibleChapterOptions);
  document.querySelector("#bibleChapter").addEventListener("change", renderBibleChapter);
  document.querySelector("#bibleSearch").addEventListener("input", renderBibleChapter);
  document.querySelector("#previousChapter").addEventListener("click", () => {
    if (!bibleBooks) return;
    let bookIndex = Number(document.querySelector("#bibleBook").value);
    let chapterIndex = Number(document.querySelector("#bibleChapter").value);
    if (chapterIndex > 0) chapterIndex -= 1;
    else if (bookIndex > 0) {
      bookIndex -= 1;
      chapterIndex = bibleBooks[bookIndex].chapters.length - 1;
      document.querySelector("#bibleBook").value = String(bookIndex);
      updateBibleChapterOptions();
    }
    document.querySelector("#bibleChapter").value = String(chapterIndex);
    renderBibleChapter();
  });
  document.querySelector("#nextChapter").addEventListener("click", () => {
    if (!bibleBooks) return;
    let bookIndex = Number(document.querySelector("#bibleBook").value);
    let chapterIndex = Number(document.querySelector("#bibleChapter").value);
    if (chapterIndex < bibleBooks[bookIndex].chapters.length - 1) chapterIndex += 1;
    else if (bookIndex < bibleBooks.length - 1) {
      bookIndex += 1;
      chapterIndex = 0;
      document.querySelector("#bibleBook").value = String(bookIndex);
      updateBibleChapterOptions();
    }
    document.querySelector("#bibleChapter").value = String(chapterIndex);
    renderBibleChapter();
  });
document.querySelector("#newVerse").addEventListener("click", refreshVerses);
document.querySelector("#refreshVerses").addEventListener("click", refreshVerses);
document.querySelector("#stopSermonPlayer").addEventListener("click", stopSermonAudio);
document.querySelector("#recordedAudio").addEventListener("play", updateSermonPlayerControls);
document.querySelector("#recordedAudio").addEventListener("pause", updateSermonPlayerControls);
document.querySelector("#recordedAudio").addEventListener("ended", stopSermonAudio);
document.querySelector("#enterBibleSelection").addEventListener("click", () => {
  if (!bibleBooks) {
    loadBible().then(renderBibleChapter).catch((error) => {
      document.querySelector("#bibleResultsNote").textContent = error.message;
      showToast(error.message);
    });
    return;
  }
  renderBibleChapter();
});
document.querySelector("#sortSermons").addEventListener("click", (event) => {
  reverseSermons = !reverseSermons;
  event.currentTarget.textContent = reverseSermons ? "↕  Oldest first" : "↕  Most recent";
  renderSermons();
});
document.querySelector("#menuToggle").addEventListener("click", () => document.querySelector("#sidebar").classList.toggle("open"));
document.querySelector("#sermonUploadTrigger").addEventListener("click", () => {
  if (!currentUser) {
    pendingPage = "studio";
    configureAuthDialog("login");
    document.querySelector("#authDialog").showModal();
    return;
  }
  document.querySelector("#sermonFileInput").click();
});
document.querySelector("#audioSermonUploadTrigger").addEventListener("click", () => {
  if (!currentUser) {
    pendingPage = "studio";
    configureAuthDialog("login");
    document.querySelector("#authDialog").showModal();
    return;
  }
  document.querySelector("#audioSermonFileInput").click();
});
document.querySelector("#shortUploadTrigger").addEventListener("click", () => {
  if (!currentUser) {
    pendingPage = "studio";
    configureAuthDialog("login");
    document.querySelector("#authDialog").showModal();
    return;
  }
  document.querySelector("#shortFileInput").click();
});
document.querySelector("#sermonCoverInput").addEventListener("change", (event) => {
  const file = event.currentTarget.files?.[0] || null;
  const label = document.querySelector("#coverUploadLabel");
  if (!file) {
    pendingSermonCover = null;
    label.textContent = "Choose sermon wallpaper (optional)";
    return;
  }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 5 * 1024 * 1024) {
    pendingSermonCover = null;
    event.currentTarget.value = "";
    label.textContent = "Choose a JPG, PNG, or WebP under 5 MB";
    showToast("Choose a JPG, PNG, or WebP sermon wallpaper under 5 MB.");
    return;
  }
  pendingSermonCover = file;
  label.textContent = file.name;
});
document.querySelector("#addPastor").addEventListener("click", () => {
  const form = document.querySelector("#pastorForm");
  form.hidden = false;
  document.querySelector("#pastorName").focus();
});
document.querySelector("#cancelAddPastor").addEventListener("click", () => {
  const form = document.querySelector("#pastorForm");
  form.reset();
  form.hidden = true;
});
document.querySelector("#searchInput").addEventListener("input", (event) => {
  updateSearch(event.target.value);
});
setupUpload("#sermonFileInput", "sermon", "Sermon");
setupUpload("#audioSermonFileInput", "audio-sermon", "Audio sermon");
setupUpload("#shortFileInput", "short", "Short video");
document.querySelector("#currentDate").textContent = new Intl.DateTimeFormat("en-US", {
  weekday: "long", month: "long", day: "numeric"
}).format(new Date()).toUpperCase();
renderSermons();
renderShorts();
renderPastors();
renderPrayerInbox();
refreshVerses();
document.querySelector("#pastorsManagement").hidden = true;
initializeServerApp();
window.setTimeout(() => {
  const startup = document.querySelector("#startupScreen");
  startup.classList.add("startup-hidden");
  window.setTimeout(() => startup.remove(), 700);
}, 900);
if (window.location.hash) openSharedContent();
window.addEventListener("hashchange", openSharedContent);
