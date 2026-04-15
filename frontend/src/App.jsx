import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import "./App.css";

const API = "http://localhost:8000/api";

const STEPS = [
  { id: 1, title: "Menu Parsing", desc: "LLM · Recipes & Ingredients" },
  { id: 2, title: "USDA Pricing", desc: "FoodData Central · Snapshots" },
  { id: 3, title: "Find Distributors", desc: "Google Places · Local supply" },
  { id: 4, title: "Send Emails", desc: "RFP Drafts · Outbound" },
];

const TABS = [
  { id: "recipes", label: "Recipes" },
  { id: "pricing", label: "Pricing" },
  { id: "distributors", label: "Distributors" },
  { id: "emails", label: "RFP Emails" },
];

const TODAY = new Date().toLocaleDateString("en-US", {
  weekday: "long",
  year: "numeric",
  month: "long",
  day: "numeric",
});

async function jfetch(url, opts) {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(check, isDone) {
  while (true) {
    if (await check()) return;
    if (isDone()) {
      await check();
      return;
    }
    await sleep(700);
  }
}

export default function App() {
  const [form, setForm] = useState({
    name: "Sweetgreen",
    source_url: "https://www.sweetgreen.com/menu",
    raw_text: "",
  });
  const [stepStatus, setStepStatus] = useState({
    1: "idle",
    2: "idle",
    3: "idle",
    4: "idle",
  });
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState("recipes");

  const [recipes, setRecipes] = useState([]);
  const [ingredients, setIngredients] = useState([]);
  const [distributors, setDistributors] = useState([]);
  const [emails, setEmails] = useState([]);

  const chartData = useMemo(
    () =>
      ingredients
        .filter((i) => i.latest_price != null)
        .slice(0, 12)
        .map((i) => ({
          name: i.name.length > 14 ? i.name.slice(0, 13) + "…" : i.name,
          price: Number(i.latest_price),
        })),
    [ingredients]
  );

  const counts = {
    recipes: recipes.length,
    pricing: ingredients.filter((i) => i.latest_price != null).length,
    distributors: distributors.length,
    emails: emails.length,
  };

  const updateField = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setStep = (id, status) =>
    setStepStatus((s) => ({ ...s, [id]: status }));

  async function handleRun(e) {
    e.preventDefault();
    if (running) return;
    setError(null);
    setRunning(true);
    setStepStatus({ 1: "running", 2: "idle", 3: "idle", 4: "idle" });
    setRecipes([]);
    setIngredients([]);
    setDistributors([]);
    setEmails([]);

    try {
      // Snapshot current counts — ingredients, distributors, and emails
      // are global tables, so we only consider a step "done" once its
      // count has grown past this baseline.
      const [baseIngs, baseDists, baseEmails] = await Promise.all([
        jfetch(`${API}/ingredients`).catch(() => []),
        jfetch(`${API}/distributors`).catch(() => []),
        jfetch(`${API}/emails`).catch(() => []),
      ]);
      const basePricedCount = baseIngs.filter((i) => i.latest_price != null).length;
      const baseDistCount = baseDists.length;
      const baseEmailCount = baseEmails.length;

      const menu = await jfetch(`${API}/menus`, {
        method: "POST",
        body: JSON.stringify(form),
      });

      // The backend's /pipeline/run blocks until the entire pipeline
      // finishes, so we fire it without awaiting and poll each step's
      // data endpoint to advance the stepper in real time.
      let pipelineDone = false;
      let pipelineError = null;
      const pipelinePromise = jfetch(`${API}/pipeline/run`, {
        method: "POST",
        body: JSON.stringify({ menu_id: menu.id, send_emails: false }),
      });
      pipelinePromise
        .catch((err) => {
          pipelineError = err;
        })
        .finally(() => {
          pipelineDone = true;
        });

      const isDone = () => pipelineDone;

      await waitFor(async () => {
        const r = await jfetch(`${API}/menus/${menu.id}/recipes`).catch(() => []);
        if (r.length) {
          setRecipes(r);
          return true;
        }
        return false;
      }, isDone);
      setStep(1, "done");
      setStep(2, "running");

      await waitFor(async () => {
        const ings = await jfetch(`${API}/ingredients`).catch(() => []);
        const priced = ings.filter((i) => i.latest_price != null).length;
        if (priced > basePricedCount) {
          setIngredients(ings);
          return true;
        }
        return false;
      }, isDone);
      setStep(2, "done");
      setStep(3, "running");

      await waitFor(async () => {
        const d = await jfetch(`${API}/distributors`).catch(() => []);
        if (d.length > baseDistCount) {
          setDistributors(d);
          return true;
        }
        return false;
      }, isDone);
      setStep(3, "done");
      setStep(4, "running");

      await waitFor(async () => {
        const em = await jfetch(`${API}/emails`).catch(() => []);
        if (em.length > baseEmailCount) {
          setEmails(em);
          return true;
        }
        return false;
      }, isDone);
      setStep(4, "done");

      await pipelinePromise;
      if (pipelineError) throw pipelineError;

      // Final refresh to catch anything added near the end.
      const [r2, i2, d2, e2] = await Promise.all([
        jfetch(`${API}/menus/${menu.id}/recipes`).catch(() => null),
        jfetch(`${API}/ingredients`).catch(() => null),
        jfetch(`${API}/distributors`).catch(() => null),
        jfetch(`${API}/emails`).catch(() => null),
      ]);
      if (r2) setRecipes(r2);
      if (i2) setIngredients(i2);
      if (d2) setDistributors(d2);
      if (e2) setEmails(e2);
    } catch (err) {
      setError(err.message || "Pipeline failed.");
      setStepStatus((s) => {
        const next = { ...s };
        for (const k of Object.keys(next)) {
          if (next[k] === "running") next[k] = "idle";
        }
        return next;
      });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <header className="masthead">
        <div className="dateline">
          Vol. I · No. 001
          <br />
          {TODAY}
        </div>
        <h1 className="title">
          The Pathway <em>Ledger</em>
        </h1>
        <div className="edition">
          Procurement Desk
          <br />
          Restaurant RFP Automation
        </div>
      </header>

      <div className="kicker">
        <span>
          <span className="dot" />
          Automated Supply Chain · Farm to Table
        </span>
        <span>Powered by USDA · OpenAI · Google Places</span>
      </div>

      <div className="body-grid">
        <aside className="stepper">
          <div className="label">The Pipeline</div>
          {STEPS.map((s) => {
            const st = stepStatus[s.id];
            return (
              <div key={s.id} className={`step ${st}`}>
                <div className="numeral">{String(s.id).padStart(2, "0")}</div>
                <div className="meta">
                  <h4 className="title">{s.title}</h4>
                  <div className="desc">{s.desc}</div>
                  <div className="status">
                    {st === "idle" && "Awaiting"}
                    {st === "running" && "In Progress"}
                    {st === "done" && "Completed"}
                  </div>
                </div>
              </div>
            );
          })}
        </aside>

        <main className="main">
          <section className="card pad">
            <div className="section-label">I. Submission</div>
            <h2 className="section-head">
              Commission a <em>new procurement</em> run
            </h2>
            <form onSubmit={handleRun}>
              <div className="form-grid">
                <div className="field">
                  <label>Restaurant Name</label>
                  <input
                    value={form.name}
                    onChange={(e) => updateField("name", e.target.value)}
                    placeholder="e.g. Sweetgreen"
                    required
                  />
                </div>
                <div className="field">
                  <label>Menu Source URL</label>
                  <input
                    value={form.source_url}
                    onChange={(e) => updateField("source_url", e.target.value)}
                    placeholder="https://…"
                  />
                </div>
                <div className="field full">
                  <label>Raw Menu Text</label>
                  <textarea
                    value={form.raw_text}
                    onChange={(e) => updateField("raw_text", e.target.value)}
                    placeholder="Paste the menu here — dish names, descriptions, ingredients. The parser will handle the rest."
                    required
                  />
                </div>
              </div>

              <div className="form-footer">
                <div className="hint">
                  Four steps · Ingredients parsed, priced, sourced, and dispatched as RFP drafts.
                </div>
                <button className="btn" disabled={running}>
                  {running ? "Processing…" : "Run the Pipeline"}
                  <span className="arrow">→</span>
                </button>
              </div>

              {error && <div className="error-banner">⚠ {error}</div>}
            </form>
          </section>

          <section className="ledger">
            <div className="section-label">II. The Ledger</div>
            <div className="tabs">
              {TABS.map((t, i) => (
                <button
                  key={t.id}
                  className={`tab ${activeTab === t.id ? "active" : ""}`}
                  onClick={() => setActiveTab(t.id)}
                  type="button"
                >
                  <span className="idx">{String(i + 1).padStart(2, "0")}.</span>
                  {t.label}
                  <span className="count">{counts[t.id]}</span>
                </button>
              ))}
            </div>

            <div className="tab-panel" key={activeTab}>
              {activeTab === "recipes" && <RecipesPanel recipes={recipes} />}
              {activeTab === "pricing" && (
                <PricingPanel
                  ingredients={ingredients}
                  chartData={chartData}
                  recipes={recipes}
                />
              )}
              {activeTab === "distributors" && (
                <DistributorsPanel distributors={distributors} />
              )}
              {activeTab === "emails" && <EmailsPanel emails={emails} />}
            </div>
          </section>

          <footer className="colophon">
            <span>
              Set in <em>Fraunces</em> &amp; JetBrains Mono
            </span>
            <span>API ↔ localhost:8000</span>
          </footer>
        </main>
      </div>
    </div>
  );
}

function RecipesPanel({ recipes }) {
  if (!recipes.length)
    return <Empty text="No recipes yet. Commission a pipeline run above." />;
  return (
    <div className="recipe-grid">
      {recipes.map((r) => (
        <article key={r.id} className="recipe">
          {r.category && <div className="cat">{r.category}</div>}
          <h3>{r.name}</h3>
          {r.description && <p className="desc">{r.description}</p>}
          <ul>
            {(r.ingredients || []).map((i) => (
              <li key={i.id}>
                <span>{i.name}</span>
                <span className="qty">
                  {i.quantity ?? "—"} {i.unit || ""}
                </span>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

// Must match backend/app/services/email_sender.py COVERS_PER_DAY and the
// conversion tables in backend/app/core/units.py.
const COVERS_PER_DAY = 150;
const DAYS_PER_WEEK = 7;

const WEIGHT_TO_OZ = {
  oz: 1, ounce: 1, ounces: 1,
  lb: 16, lbs: 16, pound: 16, pounds: 16,
  g: 0.035274, gram: 0.035274, grams: 0.035274,
  kg: 35.274, kilogram: 35.274, kilograms: 35.274,
};

const VOLUME_TO_FL_OZ = {
  "fl oz": 1, floz: 1, "fluid ounce": 1, "fluid ounces": 1,
  tsp: 1 / 6, teaspoon: 1 / 6, teaspoons: 1 / 6,
  tbsp: 0.5, tablespoon: 0.5, tablespoons: 0.5,
  cup: 8, cups: 8,
  pint: 16, pints: 16,
  quart: 32, quarts: 32,
  gallon: 128, gallons: 128, gal: 128,
  ml: 0.033814, milliliter: 0.033814, milliliters: 0.033814,
  l: 33.814, liter: 33.814, liters: 33.814,
};

const COUNT_UNITS = new Set([
  "each", "ea", "unit", "units", "piece", "pieces",
  "bunch", "bunches", "head", "heads", "clove", "cloves",
  "sprig", "sprigs", "slice", "slices",
]);

function toCanonical(qty, unit) {
  if (qty == null) return null;
  const q = Number(qty);
  if (Number.isNaN(q)) return null;
  const u = (unit || "").trim().toLowerCase().replace(/\.$/, "");
  if (!u) return null;
  if (u in WEIGHT_TO_OZ) return { category: "weight", qty: q * WEIGHT_TO_OZ[u], unit: "oz" };
  if (u in VOLUME_TO_FL_OZ) return { category: "volume", qty: q * VOLUME_TO_FL_OZ[u], unit: "fl oz" };
  if (COUNT_UNITS.has(u)) return { category: "count", qty: q, unit: "each" };
  return null;
}

function prettify(canonical) {
  const { category, qty, unit } = canonical;
  if (category === "weight" && qty >= 16) return { qty: qty / 16, unit: "lbs" };
  if (category === "volume" && qty >= 128) return { qty: qty / 128, unit: "gallons" };
  return { qty, unit };
}

function computeTotals(recipes) {
  // Per-ingredient aggregation that mirrors backend/app/core/units.py:
  // bucket rows by category, dominant category wins, others are dropped
  // to prevent mixed-unit double-counting. Result is scaled to weekly
  // procurement volume so it matches the RFP email figures.
  const buckets = {};
  for (const r of recipes) {
    for (const i of r.ingredients || []) {
      const c = toCanonical(i.quantity, i.unit);
      if (!c) continue;
      const b = (buckets[i.id] ||= { weight: [], volume: [], count: [] });
      b[c.category].push(c.qty);
    }
  }

  const totals = {};
  for (const [id, b] of Object.entries(buckets)) {
    const chosen = ["weight", "volume", "count"].reduce((a, k) =>
      b[k].length > b[a].length ? k : a
    );
    if (!b[chosen].length) continue;
    const sum = b[chosen].reduce((a, x) => a + x, 0);
    const unit =
      chosen === "weight" ? "oz" : chosen === "volume" ? "fl oz" : "each";
    const weekly = { category: chosen, qty: sum * COVERS_PER_DAY * DAYS_PER_WEEK, unit };
    totals[id] = prettify(weekly);
  }
  return totals;
}

function PricingPanel({ ingredients, chartData, recipes }) {
  if (!ingredients.length)
    return <Empty text="Pricing snapshots will appear once Step 2 completes." />;

  return (
    <div className="pricing-wrap">
      <div className="chart-box">
        <span className="chart-label">USDA Commodity Prices · Top 12</span>
        <ResponsiveContainer width="100%" height={380}>
          <BarChart data={chartData} margin={{ top: 24, right: 16, bottom: 48, left: 8 }}>
            <CartesianGrid stroke="#d6cbb0" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fontFamily: "JetBrains Mono", fontSize: 10, fill: "#7a7561" }}
              angle={-35}
              textAnchor="end"
              interval={0}
              axisLine={{ stroke: "#2a2618" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontFamily: "JetBrains Mono", fontSize: 10, fill: "#7a7561" }}
              axisLine={{ stroke: "#2a2618" }}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                background: "#161c12",
                border: "none",
                fontFamily: "JetBrains Mono",
                fontSize: 11,
                color: "#f1ebdc",
                letterSpacing: "0.05em",
              }}
              cursor={{ fill: "rgba(181,67,42,0.08)" }}
            />
            <Bar dataKey="price" fill="#2f5a2b" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="price-list">
        {(() => {
          const totals = computeTotals(recipes || []);
          // Show every ingredient — even ones USDA didn't price — so
          // dressings and unmatched items still surface with their
          // weekly procurement totals.
          return ingredients.map((i) => {
            const total = totals[i.id];
            const hasPrice = i.latest_price != null;
            return (
              <div key={i.id} className="price-row">
                <div className="ing-col">
                  <div className="ing">{i.name}</div>
                  {i.category && <div className="cat">{i.category}</div>}
                </div>
                <div className="num-col">
                  <div className={`num${hasPrice ? "" : " missing"}`}>
                    {hasPrice ? `$${Number(i.latest_price).toFixed(2)}` : "—"}
                  </div>
                  <div className="unit">
                    {hasPrice ? i.price_unit || "per lb" : "no USDA match"}
                  </div>
                  {total && (
                    <div className="total">
                      {total.qty.toFixed(1)} {total.unit}/week
                    </div>
                  )}
                </div>
              </div>
            );
          });
        })()}
      </div>
    </div>
  );
}

function DistributorsPanel({ distributors }) {
  if (!distributors.length)
    return <Empty text="Local distributors will be sourced in Step 3." />;
  return (
    <div className="dist-grid">
      {distributors.map((d) => (
        <article key={d.id} className="dist">
          {d.rating != null && (
            <div className="rating">★ {Number(d.rating).toFixed(1)}</div>
          )}
          <h3>{d.name}</h3>
          <div className="loc">
            {[d.city, d.state].filter(Boolean).join(", ") || "—"}
          </div>
          <div className="contact">
            {d.email && (
              <div>
                <a href={`mailto:${d.email}`}>{d.email}</a>
              </div>
            )}
            {d.phone && <div>{d.phone}</div>}
            {d.address && <div>{d.address}</div>}
          </div>
          {d.ingredient_count > 0 && (
            <div className="supplies">
              Supplies {d.ingredient_count} ingredient
              {d.ingredient_count === 1 ? "" : "s"}
            </div>
          )}
        </article>
      ))}
    </div>
  );
}

function EmailsPanel({ emails }) {
  if (!emails.length)
    return <Empty text="RFP drafts will appear once Step 4 completes." />;
  return (
    <div>
      {emails.map((e) => (
        <article key={e.id} className="email">
          <div className="email-head">
            <div className="to">
              To: <strong>{e.distributor_name}</strong>
              {e.distributor_email && <> · {e.distributor_email}</>}
            </div>
            <div className={`status ${e.status === "sent" ? "sent" : ""}`}>
              {e.status || "draft"}
            </div>
          </div>
          <h3>{e.subject}</h3>
          <pre>{e.body}</pre>
        </article>
      ))}
    </div>
  );
}

function Empty({ text }) {
  return <div className="empty">{text}</div>;
}
