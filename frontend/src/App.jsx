import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";
import Login from "./components/Login.jsx";
import LiveView from "./components/LiveView.jsx";
import Matches from "./components/Matches.jsx";
import Rules from "./components/Rules.jsx";
import { useLiveSocket } from "./useLiveSocket.js";

const TABS = [
  ["live", "Live sky"],
  ["matches", "My matches"],
  ["rules", "Rules"],
];

export default function App() {
  const [token, setTok] = useState(getToken());
  const [me, setMe] = useState(null);
  const [tab, setTab] = useState("live");
  const [rules, setRules] = useState([]);

  const logout = useCallback(() => {
    setToken(null);
    setTok(null);
    setMe(null);
  }, []);

  const loadRules = useCallback(async () => {
    try {
      setRules(await api("/rules"));
    } catch (e) {
      if (e.status === 401) logout();
    }
  }, [logout]);

  useEffect(() => {
    if (!token) return;
    api("/me")
      .then(setMe)
      .catch(logout);
    loadRules();
  }, [token, logout, loadRules]);

  const live = useLiveSocket(token);

  if (!token) {
    return (
      <Login
        onLogin={(t) => {
          setToken(t);
          setTok(t);
        }}
      />
    );
  }

  return (
    <>
      <header className="topbar">
        <h1>SkyAlert</h1>
        <nav aria-label="Sections">
          {TABS.map(([id, label]) => (
            <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>
              {label}
              {id === "matches" && live.unseenMatches > 0 ? ` (${live.unseenMatches})` : ""}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <span className={`status ${live.connected ? "on" : ""}`}>
          <i aria-hidden="true" />
          {live.connected ? "Receiving alerts" : "Reconnecting…"}
        </span>
        <span className="muted small">{me?.email}</span>
        <button className="quiet" onClick={logout}>
          Log out
        </button>
      </header>
      <main>
        {tab === "live" && <LiveView live={live} rules={rules} />}
        {tab === "matches" && <Matches live={live} rules={rules} />}
        {tab === "rules" && <Rules rules={rules} reload={loadRules} />}
      </main>
    </>
  );
}
