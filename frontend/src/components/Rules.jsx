import { useState } from "react";
import { api } from "../api.js";

const FIELDS = {
  magpsf: "Magnitude",
  sigmapsf: "Magnitude error",
  rb: "Real/bogus score",
  ndethist: "Number of detections",
  fid: "Filter (1=g, 2=r, 3=i)",
  ra: "RA (deg)",
  dec: "Dec (deg)",
  jd: "Julian date",
};
const OPS = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", neq: "≠" };

const PRESETS = {
  "Bright and real": {
    conditions: [
      { field: "magpsf", op: "lt", value: 18.5 },
      { field: "rb", op: "gte", value: 0.8 },
    ],
    cone: null,
  },
  "Anything near M31": {
    conditions: [{ field: "rb", op: "gte", value: 0.6 }],
    cone: { ra: 10.6847, dec: 41.269, radius_arcsec: 1800 },
  },
  "New object in M101": {
    conditions: [{ field: "ndethist", op: "lte", value: 2 }],
    cone: { ra: 210.8023, dec: 54.3489, radius_arcsec: 900 },
  },
};

const blank = () => ({ name: "", conditions: [{ field: "magpsf", op: "lt", value: 19 }], cone: null, webhook_url: "", active: true });

function describeCondition(c) {
  return `${FIELDS[c.field] ?? c.field} ${OPS[c.op]} ${c.value}`;
}

export default function Rules({ rules, reload }) {
  const [draft, setDraft] = useState(blank);
  const [editingId, setEditingId] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const update = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const setCond = (i, patch) =>
    update({ conditions: draft.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    const body = {
      ...draft,
      webhook_url: draft.webhook_url || null,
      conditions: draft.conditions.map((c) => ({ ...c, value: Number(c.value) })),
      cone: draft.cone
        ? { ra: Number(draft.cone.ra), dec: Number(draft.cone.dec), radius_arcsec: Number(draft.cone.radius_arcsec) }
        : null,
    };
    try {
      await api(editingId ? `/rules/${editingId}` : "/rules", { method: editingId ? "PUT" : "POST", body });
      setDraft(blank());
      setEditingId(null);
      await reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const edit = (r) => {
    setEditingId(r.id);
    setDraft({ ...r, webhook_url: r.webhook_url ?? "" });
    setError("");
  };

  const toggle = async (r) => {
    await api(`/rules/${r.id}`, { method: "PUT", body: { ...r, active: !r.active } }).catch((e) => setError(e.message));
    reload();
  };

  const remove = async (r) => {
    if (!window.confirm(`Delete the rule "${r.name}"? Its past matches are deleted too.`)) return;
    await api(`/rules/${r.id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    if (editingId === r.id) {
      setEditingId(null);
      setDraft(blank());
    }
    reload();
  };

  return (
    <div className="rules-grid">
      <section className="panel">
        <div className="panel-head">
          <h2>{editingId ? "Edit rule" : "New rule"}</h2>
          {!editingId && (
            <select
              aria-label="Start from an example"
              value=""
              onChange={(e) => e.target.value && update({ name: e.target.value, ...PRESETS[e.target.value] })}
            >
              <option value="">Start from an example…</option>
              {Object.keys(PRESETS).map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          )}
        </div>
        <form className="rule-form" onSubmit={save}>
          <label>
            Name
            <input required maxLength={100} value={draft.name} onChange={(e) => update({ name: e.target.value })} />
          </label>

          <fieldset>
            <legend>Match alerts where all of these are true</legend>
            {draft.conditions.map((c, i) => (
              <div className="row" key={i}>
                <select aria-label="Field" value={c.field} onChange={(e) => setCond(i, { field: e.target.value })}>
                  {Object.entries(FIELDS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
                <select aria-label="Comparison" value={c.op} onChange={(e) => setCond(i, { op: e.target.value })}
                  style={{ flex: "0 0 64px" }}>
                  {Object.entries(OPS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
                <input aria-label="Value" type="number" step="any" required value={c.value}
                  onChange={(e) => setCond(i, { value: e.target.value })} />
                <button type="button" className="quiet" aria-label="Remove condition"
                  onClick={() => update({ conditions: draft.conditions.filter((_, j) => j !== i) })}>
                  ✕
                </button>
              </div>
            ))}
            <div>
              <button type="button" disabled={draft.conditions.length >= 20}
                onClick={() => update({ conditions: [...draft.conditions, { field: "rb", op: "gte", value: 0.7 }] })}>
                Add condition
              </button>
            </div>
          </fieldset>

          <fieldset>
            <legend>Only in this part of the sky</legend>
            <label className="check">
              <input type="checkbox" checked={!!draft.cone}
                onChange={(e) => update({ cone: e.target.checked ? { ra: 10.6847, dec: 41.269, radius_arcsec: 600 } : null })} />
              Limit to a circle on the sky
            </label>
            {draft.cone && (
              <div className="row">
                <label>RA (deg)
                  <input type="number" step="any" min={0} max={359.9999} required value={draft.cone.ra}
                    onChange={(e) => update({ cone: { ...draft.cone, ra: e.target.value } })} />
                </label>
                <label>Dec (deg)
                  <input type="number" step="any" min={-90} max={90} required value={draft.cone.dec}
                    onChange={(e) => update({ cone: { ...draft.cone, dec: e.target.value } })} />
                </label>
                <label>Radius (arcsec)
                  <input type="number" step="any" min={1} max={36000} required value={draft.cone.radius_arcsec}
                    onChange={(e) => update({ cone: { ...draft.cone, radius_arcsec: e.target.value } })} />
                </label>
              </div>
            )}
          </fieldset>

          <label>
            Webhook URL (optional)
            <input type="url" placeholder="https://example.com/my-hook" value={draft.webhook_url}
              onChange={(e) => update({ webhook_url: e.target.value })} />
            <span className="small">Each match is sent here as a JSON POST, with retries if your server is down.</span>
          </label>

          {error && <p className="error" role="alert">{error}</p>}
          <div className="form-actions">
            <button className="primary" disabled={busy}>{editingId ? "Save changes" : "Create rule"}</button>
            {editingId && (
              <button type="button" onClick={() => (setEditingId(null), setDraft(blank()), setError(""))}>
                Cancel
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Your rules</h2>
          <span className="muted small">{rules.length} of 100</span>
        </div>
        {rules.length === 0 && <p className="empty">You have no rules yet. Create one to start getting matches.</p>}
        {rules.map((r) => (
          <article key={r.id} className={`rule-card ${r.active ? "" : "off"}`}>
            <header>
              <h3>{r.name}</h3>
              <div className="row" style={{ flex: "0 0 auto" }}>
                <button className="quiet" onClick={() => toggle(r)}>{r.active ? "Pause" : "Resume"}</button>
                <button className="quiet" onClick={() => edit(r)}>Edit</button>
                <button className="quiet" onClick={() => remove(r)}>Delete</button>
              </div>
            </header>
            <div className="chips">
              {r.conditions.map((c, i) => (
                <span className="chip" key={i}>{describeCondition(c)}</span>
              ))}
              {r.cone && (
                <span className="chip">
                  within {r.cone.radius_arcsec}″ of {r.cone.ra.toFixed(3)}, {r.cone.dec.toFixed(3)}
                </span>
              )}
              {r.conditions.length === 0 && !r.cone && <span className="chip">every alert</span>}
            </div>
            {r.webhook_url && <p className="small muted">Sends to {r.webhook_url}</p>}
          </article>
        ))}
      </section>
    </div>
  );
}
