const form = document.getElementById("search-form");
const promptInput = document.getElementById("prompt");
const submitButton = document.getElementById("submit-button");
const statusEl = document.getElementById("status");
const resultPanel = document.getElementById("result-panel");
const normalizedQuestionEl = document.getElementById("normalized-question");
const resultCountEl = document.getElementById("result-count");
const summaryEl = document.getElementById("summary");
const pubmedQueryEl = document.getElementById("pubmed-query");
const articlesEl = document.getElementById("articles");
const articleTemplate = document.getElementById("article-template");
const historyEl = document.getElementById("history");
const historyTemplate = document.getElementById("history-template");

function logEvent(event, details = {}) {
  console.log(`[ui] ${event}`, details);
}

function setLoading(isLoading, message = "") {
  logEvent("loading.change", { isLoading, message });
  submitButton.disabled = isLoading;
  submitButton.textContent = isLoading ? "Searching..." : "Search PubMed";
  statusEl.textContent = message;
}

function renderArticles(articles) {
  logEvent("articles.render", { count: articles.length });
  articlesEl.innerHTML = "";
  for (const article of articles) {
    const fragment = articleTemplate.content.cloneNode(true);
    const meta = fragment.querySelector(".article-meta");
    const title = fragment.querySelector(".article-title");
    const authors = fragment.querySelector(".article-authors");
    const link = fragment.querySelector(".article-link");

    meta.textContent = [article.journal, article.pubdate, `PMID ${article.pmid}`]
      .filter(Boolean)
      .join(" · ");
    title.textContent = article.title || "Untitled article";
    authors.textContent = article.authors?.length
      ? article.authors.join(", ")
      : "Authors unavailable";
    link.href = article.url;

    articlesEl.appendChild(fragment);
  }
}

function renderError(message) {
  logEvent("search.error", { message });
  resultPanel.classList.remove("hidden");
  normalizedQuestionEl.textContent = "Input needs correction";
  resultCountEl.textContent = "0";
  summaryEl.textContent = message;
  pubmedQueryEl.textContent = "";
  articlesEl.innerHTML = "";
}

function renderHistory(items) {
  logEvent("history.render", { count: items.length });
  historyEl.innerHTML = "";

  if (!items.length) {
    historyEl.textContent = "No searches stored yet.";
    return;
  }

  for (const item of items) {
    const fragment = historyTemplate.content.cloneNode(true);
    const meta = fragment.querySelector(".article-meta");
    const title = fragment.querySelector(".history-title");
    const query = fragment.querySelector(".history-query");

    meta.textContent = [item.created_at, item.status, `${item.result_count} results`]
      .filter(Boolean)
      .join(" · ");
    title.textContent = item.normalized_question || item.original_prompt || "Untitled search";
    query.textContent = item.pubmed_query || "No PubMed query stored";

    historyEl.appendChild(fragment);
  }
}

async function loadHistory() {
  logEvent("history.load.start");
  try {
    const response = await fetch("/api/history");
    const data = await response.json();
    logEvent("history.load.response", { httpOk: response.ok, appOk: data.ok, count: (data.items || []).length });
    if (response.ok && data.ok) {
      renderHistory(data.items || []);
    }
  } catch (error) {
    logEvent("history.load.error", { error: String(error) });
    historyEl.textContent = "Search history is unavailable.";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput.value.trim();
  logEvent("search.submit", { promptLength: prompt.length });

  if (!prompt) {
    renderError("Enter a biomedical prompt before searching.");
    return;
  }

  setLoading(true, "Validating prompt and fetching research...");
  try {
    logEvent("search.request.start");
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });

    const data = await response.json();
    logEvent("search.request.response", {
      httpOk: response.ok,
      appOk: data.ok,
      status: data.status,
      resultCount: data.result_count,
    });
    resultPanel.classList.remove("hidden");

    if (!response.ok || !data.ok) {
      renderError(data.message || "The input could not be processed.");
      return;
    }

    normalizedQuestionEl.textContent = data.normalized_question || data.original_prompt;
    resultCountEl.textContent = String(data.result_count ?? 0);
    summaryEl.textContent = data.message || "";
    pubmedQueryEl.textContent = data.pubmed_query || "";
    renderArticles(data.articles || []);
    await loadHistory();
    logEvent("search.success", { resultCount: data.result_count ?? 0 });
    statusEl.textContent = data.result_count
      ? `Loaded ${data.result_count} matching PubMed records.`
      : "No matching PubMed records found.";
  } catch (error) {
    logEvent("search.request.error", { error: String(error) });
    renderError("The app could not reach the local server or an upstream API.");
  } finally {
    setLoading(false, statusEl.textContent);
  }
});

logEvent("app.init");
loadHistory();
