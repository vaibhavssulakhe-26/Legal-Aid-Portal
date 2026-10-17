const inputEl = document.getElementById("searchInput");
const stateEl = document.getElementById("stateFilter");
const webEl = document.getElementById("webToggle");
const chipsEl = document.getElementById("chips");
const boxEl = document.getElementById("answerBox");
const matchedEl = document.getElementById("matched");
const answerEl = document.getElementById("answer");
const helpEl = document.getElementById("helpbar");
const stateInfoEl = document.getElementById("stateInfo");
const sourcesEl = document.getElementById("sources");
const contactListEl = document.getElementById("contactList");

let busy = false;

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}
function fmt(t) {
  return escapeHtml(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
}

function renderHelp(list) {
  helpEl.innerHTML = `<h4>Call for help</h4><div class="helprow">` +
    list.map(c => `<a class="call big" href="tel:${c.number}">${escapeHtml(c.name)}<br><strong>${c.number}</strong></a>`).join("") +
    `</div>`;
}

function renderState(s) {
  if (!s) { stateInfoEl.innerHTML = ""; return; }
  const phone = s.phone ? `Phone: <a href="tel:${s.phone}">${escapeHtml(s.phone)}</a>` : "Contact number: look it up on nalsa.gov.in (not yet verified here)";
  const site = s.website ? ` | <a href="${s.website}" target="_blank" rel="noopener">Website</a>` : "";
  stateInfoEl.innerHTML = `
    <div class="stateinfo">
      <h4>Free legal aid in ${escapeHtml(s.state)}</h4>
      <p><strong>${escapeHtml(s.authority)}</strong><br>${phone}${site}</p>
      <p><small>Every district also has a District Legal Services Authority (DLSA) at the district court. Ask there for free legal aid.</small></p>
    </div>`;
}

function renderSources(list) {
  if (!list.length) { sourcesEl.innerHTML = ""; return; }
  sourcesEl.innerHTML = `<h4>Web sources used (check before relying on them)</h4><ul>` +
    list.map(s => `<li><a href="${s.url}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a></li>`).join("") + `</ul>`;
}

async function ask(question) {
  if (busy) return;
  const q = (question || inputEl.value).trim();
  if (!q) return;
  inputEl.value = q;
  busy = true;

  boxEl.style.display = "block";
  matchedEl.innerHTML = "";
  answerEl.innerHTML = `<span class="thinking">Thinking... the first answer can take up to a minute.</span>`;
  helpEl.innerHTML = ""; stateInfoEl.innerHTML = ""; sourcesEl.innerHTML = "";

  const params = new URLSearchParams({ q, state: stateEl.value, web: webEl.checked ? "1" : "0" });
  let text = "";
  try {
    const res = await fetch(`/api/ask?${params}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const l of lines) {
        if (!l.trim()) continue;
        const msg = JSON.parse(l);
        if (msg.type === "meta") {
          renderHelp(msg.helplines);
          renderState(msg.state);
          renderSources(msg.sources);
          matchedEl.innerHTML = msg.matched.length
            ? `<small>Based on verified guides: ${msg.matched.map(escapeHtml).join(" · ")}</small>`
            : `<small>No verified guide matched closely. Please double-check this answer.</small>`;
        } else if (msg.type === "token") {
          text += msg.text;
          answerEl.innerHTML = fmt(text);
        } else if (msg.type === "error") {
          answerEl.innerHTML = `<span class="err">${escapeHtml(msg.text)}</span>`;
        }
      }
    }
  } catch (e) {
    answerEl.innerHTML = `<span class="err">Something went wrong: ${escapeHtml(String(e))}</span>`;
  }
  busy = false;
}

async function init() {
  const [contacts, states] = await Promise.all([
    fetch("/api/contacts").then(r => r.json()),
    fetch("/api/states").then(r => r.json())
  ]);
  states.forEach(s => stateEl.insertAdjacentHTML("beforeend", `<option>${escapeHtml(s.state)}</option>`));
  contactListEl.innerHTML = contacts.map(c => `
    <div class="contact">
      <div><strong>${escapeHtml(c.name)}</strong><br><small>${escapeHtml(c.note || "")}</small></div>
      <a class="call" href="tel:${c.number}">${c.number}</a>
    </div>`).join("");

  const common = [
    "My landlord won't return my deposit",
    "Someone cheated me online and took my money",
    "My employer hasn't paid my salary",
    "Police refused to file my complaint",
    "I need a death certificate",
    "I am being harassed",
    "I cannot afford a lawyer",
    "A child is missing"
  ];
  chipsEl.innerHTML = common.map(c => `<button class="chip">${escapeHtml(c)}</button>`).join("");
  chipsEl.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => ask(b.textContent)));
}

document.getElementById("searchBtn").addEventListener("click", () => ask());
inputEl.addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
init();
